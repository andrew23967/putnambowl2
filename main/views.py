import json
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db.models import Prefetch
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from . import forms, scrape
from .models import (
    Game, IntroTemplate, Pick, SiteSettings, WeeklyLeaderboard,
    LeagueEmail,
)
from .rankings import competition_ranks, rank_rows
from .scoring import calculate_points
from .teams import team_from_abbrev

log = logging.getLogger(__name__)


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
                'short': 'Locks in:',
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
            # The units under the digits already say this is a countdown, so the
            # matchup alone carries the whole message when space is short.
            'short': matchup,
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
    `inbound_email`, and mail the site sends is recorded at send time. That keeps
    ordering honest, because every row carries a real `sent_at`; a feed stitched
    together from `WeeklyLeaderboard.recap` had no timestamp to sort by.
    """
    qs = LeagueEmail.objects.filter(published=True).select_related('author', 'author__profile')
    return list(qs[:limit] if limit else qs)


def home(request):
    if not request.user.is_authenticated:
        return redirect('accounts:login')

    settings = SiteSettings.get()
    # Open until week 1's picks lock — the same deadline as the slate above it in
    # the bar, so the countdown there is the countdown for these too. Shown in
    # both states meanwhile: a prompt before they are in, the way back in after.
    preseason_open = settings.week == 1 and not settings.lock_picks

    # Nudge to the preseason form, but only while it can still be filled in.
    # This used to test `settings.week == 1` alone, so anyone who missed the
    # deadline was bounced to a form that would no longer accept anything — and
    # bounced again on every visit, with no way to reach the home page until the
    # week rolled over. "Later" sets a session flag, but a new browser session
    # forgot it and the trap closed again.
    needs_preseason = (preseason_open
                       and not request.user.profile.preseason_submitted)
    if needs_preseason and not request.session.get('preseason_deferred'):
        return redirect('main:preseason')

    players = User.objects.select_related('profile').all()

    leaderboard = sorted(
        [{'score': round(p.profile.score, 1), 'username': p.username} for p in players],
        key=lambda x: x['score'], reverse=True
    )

    # Rank change vs previous week. Both sides use competition ranking, so two
    # players tied all season do not show a change every week as their arbitrary
    # order flips.
    rank_rows(leaderboard)
    prev_ranks = {}
    if settings.week > 1:
        try:
            prev_lb = WeeklyLeaderboard.objects.get(week=settings.week - 1)
            prev_ranks = competition_ranks(
                (e['username'], e['score']) for e in prev_lb.entries)
        except WeeklyLeaderboard.DoesNotExist:
            pass
    for entry in leaderboard:
        prev = prev_ranks.get(entry['username'])
        change = (prev - entry['rank']) if prev else 0
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

    feed = _email_feed()
    countdown = _countdown(settings, games)

    # allow_network=False is required in a page view: the week-number fallback is
    # accurate for 2021+ and the download costs seconds on every home page load.
    week_type = scrape.get_week_type(settings.week, allow_network=False)
    week_type_label = {
        'regular': 'Regular Season',
        'playoffs': 'Playoffs',
        'superbowl': 'Super Bowl',
    }.get(week_type, 'Regular Season')

    return render(request, 'main/home.html', {
        'countdown_json': countdown['milestones_json'],
        'countdown_idle': countdown['idle_label'],
        'leaderboard': leaderboard,
        'leaderboard_json': json.dumps(leaderboard),
        'settings': settings,
        'emails': feed,
        'email_count': len(feed),
        'season_year': scrape.current_season_year(),
        'week_type_label': week_type_label,
        'total_games': len(games),
        'picks_made': len(my_picks),
        'my_correct': sum(1 for p in my_picks if p.is_correct),
        'graded_count': sum(1 for g in games if g.winner),
        'preseason_open': preseason_open,
        'preseason_done': request.user.profile.preseason_submitted,
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
        rank_map = competition_ranks(
            (e['username'], e['score']) for e in lb.entries)
        points_chart.append([str(lb.week)] + [score_map.get(u, 0) for u in chart_players])
        position_chart.append([str(lb.week)] + [rank_map.get(u, len(chart_players)) for u in chart_players])

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
        eff_row = [str(wk_num)]
        for u in chart_players:
            eff_row.append(round(week_earned[u] / week_potential * 100, 1) if week_potential else 0)
        efficiency_chart.append(eff_row)

    completed_weeks = sorted(games_by_past_week.keys())

    return render(request, 'main/analytics.html', {
        'settings': settings,
        'completed_weeks': completed_weeks,
        'points_chart': json.dumps(points_chart),
        'position_chart': json.dumps(position_chart),
        'efficiency_chart': json.dumps(efficiency_chart),
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
    if live_grading:
        # Movement caused by this week's results so far. The baseline is each
        # player's stored score, which is exactly "where they stood when the week
        # locked" - profile.score is only written by do_advance_week, so it does
        # not move again until the week rolls over. `_base` already carries it.
        #
        # This used to read WeeklyLeaderboard(week=settings.week), which does not
        # exist yet: that row is written when the week is advanced *away* from,
        # so it only appears once the week is over. The lookup always missed, and
        # every arrow came back flat - so the page rendered real movement and the
        # first live poll wiped it to dashes.
        prev_ranks = competition_ranks((e['username'], e['_base']) for e in entries)
        prev_scores = {e['username']: e['_base'] for e in entries}
    else:
        # Otherwise compare against the snapshot from before the previous week,
        # which is what the home page itself does. WeeklyLeaderboard(week=N)
        # holds the scores as they stood going *into* week N.
        baseline_week = week - 1
        if baseline_week > 0:
            try:
                prev_lb = WeeklyLeaderboard.objects.get(week=baseline_week)
                prev_ranks = competition_ranks(
                    (e['username'], e['score']) for e in prev_lb.entries)
                prev_scores = {e['username']: e['score'] for e in prev_lb.entries}
            except WeeklyLeaderboard.DoesNotExist:
                pass

    is_past = week < settings.week
    current_user = request.user.username
    # Ranks come from the scores, not from row position, so ties share a place.
    ranks = competition_ranks((e['username'], e['score']) for e in entries)
    result = []
    for entry in entries:
        rank = ranks[entry['username']]
        prev = prev_ranks.get(entry['username'])
        change = (prev - rank) if prev else 0
        if live_grading:
            gained = entry.get('_gained', 0)
        elif is_past:
            gained = round(entry['score'] - prev_scores.get(entry['username'], entry['score']), 1)
        else:
            gained = None
        result.append({
            'username': entry['username'],
            'score': entry['score'],
            'rank': rank,
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
    points2 = (calculate_points(ug_ml, abs(fav_ml)) * settings.multiplier
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
        team1_is_home=d['favorite_is_home'],
        game_dt=game_dt_utc,
        week=settings.week,
    )
    return JsonResponse({'ok': True, 'game': {
        'id': game.id,
        'team1_abbrev': game.team1_abbrev,
        'team2_abbrev': game.team2_abbrev,
        'points1': game.points1,
        'points2': game.points2,
        'team1_is_home': game.team1_is_home,
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

    countdown = _countdown(settings, games)

    return render(request, 'main/picks.html', {
        'settings': settings,
        'games': games,
        'picks_map': picks_map,
        'graded_count': sum(1 for g in games if g.winner),
        'countdown_json': countdown['milestones_json'],
        'countdown_idle': countdown['idle_label'],
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
        'team1_is_home': g.team1_is_home,
        'game_dt_iso': g.game_dt_iso,
    } for g in games]

    all_usernames = list(User.objects.order_by('username').values_list('username', flat=True))

    rank_after = competition_ranks(week_scores)
    rank_before = competition_ranks(pre_scores)

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
    # Submitting is not the deadline; week 1's kickoff is. These stay editable
    # right up until that week's picks lock, and close with them — past that the
    # season has started and a late edit would be hindsight.
    preseason_open = settings.week == 1 and not settings.lock_picks

    if request.method == 'POST' and 'defer' in request.POST:
        request.session['preseason_deferred'] = True
        return redirect('main:home')

    if request.method == 'POST' and not preseason_open:
        messages.error(request, 'Preseason picks locked when week 1 picks did.')
        return redirect('main:home')

    form = forms.PreseasonForm(request.user, request.POST or None)
    if preseason_open and form.is_valid():
        request.user.profile.big_loser = form.cleaned_data['big_loser']
        request.user.profile.nfc_champ = form.cleaned_data['nfc_champ']
        request.user.profile.afc_champ = form.cleaned_data['afc_champ']
        request.user.profile.superbowl_winner = form.cleaned_data['superbowl_winner']
        request.user.profile.preseason_submitted = True
        request.user.profile.save()
        request.session.pop('preseason_deferred', None)
        # No flash on the way out: the bar on the home page turns green and says
        # the picks are in, which is the same news in the place it belongs, and
        # it keeps saying it rather than fading after one page load.
        return redirect('main:home')
    return render(request, 'main/preseason.html', {
        'form': form,
        'week': settings.week,
        'preseason_open': preseason_open,
        'preseason_done': request.user.profile.preseason_submitted,
        # Only worth offering while they are still missing; once they are in this
        # page is an edit screen, and there is nothing left to put off.
        'can_defer': preseason_open and not request.user.profile.preseason_submitted,
    })


def rules(request):
    """The commissioner's rules, kept verbatim in the template.

    They were a list of (title, body) pairs paraphrased here in the view. That
    is prose, not data — nothing queries it and nothing varies it — and the
    paraphrase had drifted from what the league was actually told. The template
    now carries the original text from the old site instead.
    """
    return render(request, 'main/rules.html')



@login_required
def members(request):
    """The league roster: who is in it, when they joined, what they wrote.

    Ordered by join date, oldest first, so the page reads as the league's history
    rather than an alphabetical list. Bots are included — they play and they
    score, so leaving them out would make the standings not add up — but they are
    marked, and sorted last regardless of when they were created.
    """
    people = (User.objects.select_related('profile')
              .order_by('date_joined', 'username'))
    rows = []
    for user in people:
        profile = user.profile
        rows.append({
            'username': user.username,
            'display_name': profile.display_name,
            'bio': profile.bio.strip(),
            'joined': user.date_joined,
            'team': profile.favorite_team,
            'team_abbrev': profile.favorite_team_abbrev,
            'score': profile.score_display,
            'theme': profile.theme or '#00897b',
            'initial': (profile.display_name or user.username)[:1].upper(),
            'is_bot': profile.is_bot,
            # Only when they actually submitted. Every preseason field has a team
            # as its default, so an untouched profile would otherwise claim four
            # confident picks nobody made.
            'preseason': {
                'big_loser': profile.big_loser,
                'nfc': profile.nfc_champ,
                'afc': profile.afc_champ,
                'superbowl': profile.superbowl_winner,
            } if profile.preseason_submitted else None,
        })
    rows.sort(key=lambda r: (r['is_bot'], r['joined']))

    return render(request, 'main/members.html', {
        'members': rows,
        'human_count': sum(1 for r in rows if not r['is_bot']),
        'bot_count': sum(1 for r in rows if r['is_bot']),
    })


@staff_member_required
def emaildash(request):
    """Which emails go out, and what the recap is told to say.

    Only the *instructions* are editable. Each prompt's data — standings, results,
    and the output format rules — is appended by the code and shown here read-only,
    so it cannot be edited away by accident.
    """
    from django.conf import settings as django_settings

    from . import auto
    from .email_utils import (intro_address, league_recipients, picks_address,
                              smtp_ready)

    settings = SiteSettings.get()

    if request.method == 'POST':
        if 'save_switches' in request.POST:
            for field in ('email_picks_live', 'email_ballot', 'email_recap',
                          'email_reminder', 'email_confirmations', 'email_relay'):
                setattr(settings, field, request.POST.get(field) == 'on')
            try:
                settings.reminder_hours_before_lock = max(1, min(168, int(
                    request.POST.get('reminder_hours_before_lock', 24))))
            except (TypeError, ValueError):
                pass
            settings.save()
            messages.success(request, 'Email settings saved.')
        elif 'save_intro' in request.POST:
            settings.weekly_intro = request.POST.get('weekly_intro', '').strip()
            settings.save(update_fields=['weekly_intro'])
            messages.success(
                request,
                'Intro saved. It goes out at the top of this week\'s email.'
                if settings.weekly_intro else 'Intro cleared.')
        elif 'use_intro' in request.POST:
            # Copy the template's raw body, `{week}` and all — it is substituted
            # when the mail is built, so the text stays reusable and the copy can
            # be edited for this week without touching the saved one.
            tpl = IntroTemplate.objects.filter(
                pk=request.POST.get('use_intro')).first()
            if tpl:
                settings.weekly_intro = tpl.body
                settings.save(update_fields=['weekly_intro'])
                messages.success(request, f'Using "{tpl.name}" this week.')
        elif 'save_template' in request.POST:
            name = request.POST.get('tpl_name', '').strip()
            body = request.POST.get('tpl_body', '').strip()
            pk = request.POST.get('tpl_id') or None
            if not name or not body:
                messages.error(request, 'An intro needs both a name and some text.')
            elif IntroTemplate.objects.filter(name=name).exclude(pk=pk).exists():
                messages.error(request, f'There is already an intro called "{name}".')
            elif pk:
                IntroTemplate.objects.filter(pk=pk).update(name=name, body=body)
                messages.success(request, f'Saved "{name}".')
            else:
                IntroTemplate.objects.create(name=name, body=body)
                messages.success(request, f'Added "{name}".')
        elif 'delete_template' in request.POST:
            tpl = IntroTemplate.objects.filter(
                pk=request.POST.get('delete_template')).first()
            if tpl:
                name = tpl.name
                tpl.delete()
                messages.success(request, f'Deleted "{name}".')
        elif 'save_prompts' in request.POST:
            settings.recap_prompt = request.POST.get('recap_prompt', '').strip()
            settings.save()
            messages.success(request, 'Prompts saved.')
        elif 'reset_prompts' in request.POST:
            settings.recap_prompt = ''
            settings.save()
            messages.success(request, 'Prompts reset to the built-in defaults.')
        return redirect('main:emaildash')

    # Preview the data block against the most recently completed week, so what is
    # shown is real rather than illustrative.
    preview_week = max(settings.week - 1, 1)
    data_block, _ = auto.recap_data_block(preview_week)

    # Who the reminder would go to if it fired now - the point of the feature is
    # knowing that before it sends, not after.
    from .email_utils import members_missing_picks
    outstanding = members_missing_picks(settings.week)
    reminder_due_dt = None
    if settings.auto_lock_dt:
        reminder_due_dt = settings.auto_lock_dt - timedelta(
            hours=settings.reminder_hours_before_lock or 0)

    # Rendered with this week's number so what is shown is what would go out.
    intro_preview = (settings.weekly_intro or '').replace('{week}', str(settings.week))

    return render(request, 'main/emaildash.html', {
        'settings': settings,
        'intro_templates': IntroTemplate.objects.all(),
        'intro_preview': intro_preview,
        'outstanding': [(u.username, made, total) for u, made, total in outstanding],
        'reminder_due_dt': reminder_due_dt,
        'recipients': league_recipients(),
        # Members only. The mailbox is deliberately not in this list: BCC is
        # stripped in transit, so copying both would have the relay forward to
        # everyone a second time.
        'recipient_list': ', '.join(league_recipients()),
        # Mail to the league address only counts as an announcement when the
        # sender is set to publish; otherwise inbound reads it as picks.
        'viewer_can_publish_by_email': request.user.profile.email_posts_enabled,
        'mailbox': getattr(django_settings, 'SMTP_USER', '') or '',
        'picks_address': picks_address(),
        'intro_address': intro_address(),
        'smtp_ready': smtp_ready(),
        'recap_prompt': settings.recap_prompt or auto.DEFAULT_RECAP_PROMPT,
        'recap_is_default': not settings.recap_prompt,
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

    # Explicit POST only. This used to run on every GET whenever auto_enabled was
    # set, which made *opening this page* scrape the week, publish it and mail
    # the league — a page view with irreversible outward side effects. It fired
    # for real from throwaway database copies too, because the SMTP credentials
    # are the same whichever database is attached, and it sent to real members.
    #
    # `run_auto` is what should drive the autopilot. The button exists so a tick
    # can still be forced from here when the worker is not running, but only
    # because someone pressed it.
    if settings.auto_enabled and request.method == 'POST' and 'run_tick' in request.POST:
        try:
            from .auto import auto_tick
            auto_tick()
            settings = SiteSettings.get()
            messages.success(request, 'Auto-pilot tick run.')
        except Exception as _e:
            log.exception('manual auto_tick failed')
            messages.error(request, f'Auto-pilot tick failed: {_e}')

    if 'delete_all_games' in request.POST:
        current_games = Game.objects.filter(week=settings.week)
        Pick.objects.filter(game__in=current_games).delete()
        current_games.delete()

    elif 'toggle_publish' in request.POST:
        # Publishing is what the league is waiting to hear about, and for most
        # weeks this button is how it happens: the Scrape button does not publish,
        # and only mails when the week was already published. So flipping this on
        # sent nothing at all, which is the whole point of the ballot.
        was_published = settings.publish
        settings.publish = not settings.publish
        if not settings.publish:
            # Taking a week down has to take it off the autopilot's list too.
            # With auto_scrape_dt left in the past, the next tick re-scraped,
            # re-published and mailed the league again. Re-publishing is by
            # hand from here; the autopilot resumes at the next advance.
            settings.auto_scrape_dt = None
            settings.auto_first_attempt_dt = None
            settings.auto_last_issue = ''
        settings.save()
        if settings.publish and not was_published:
            from .email_utils import send_picks_published_email
            send_picks_published_email(settings)

    elif 'toggle_lock' in request.POST:
        settings.lock_picks = not settings.lock_picks
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
            # Game days: a set of checkboxes now, not a from/to range, so a
            # league can play Sunday and Monday without picking up Saturday.
            picked = request.POST.getlist('scrape_days')
            days = sorted({int(d) for d in picked if d.isdigit() and 0 <= int(d) <= 6})
            settings.scrape_days = '' if len(days) in (0, 7) else ','.join(str(d) for d in days)

            settings.auto_advance = 'auto_advance' in request.POST
            settings.season_last_week = max(1, min(30, int(
                request.POST.get('season_last_week', 22))))
            settings.auto_retry_window_minutes = max(0, min(2880, int(
                request.POST.get('auto_retry_window_minutes', 360))))

            from .auto import _this_or_next_weekday_hour
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
        # The same day filter the autopilot uses. This button had none, so it
        # pulled every game in the week whatever `scrape_days` said - and since
        # the lock is derived from the earliest kickoff actually stored, one
        # Thursday nighter nobody picks dragged the deadline two days early.
        from .auto import _game_day_allowed
        day_set = settings.scrape_day_set()
        added = dupes = skipped_day = 0
        for g in games:
            if not _game_day_allowed(g[6], day_set, settings.auto_tz):
                skipped_day += 1
                continue
            # team_from_abbrev, not a bare dict lookup: 'LA' is not a key in
            # ABBREV_TO_TEAM, so a Rams game was stored under the literal name
            # "LA" and could never match the same fixture stored properly.
            team1 = team_from_abbrev(g[0])
            team2 = team_from_abbrev(g[1])
            game_id = g[5]
            ml1, ml2 = g[2], g[3]
            pts2 = (calculate_points(ml1, abs(ml2)) * settings.multiplier
                    if ml1 and ml2 else float(settings.multiplier))

            # Match on who is playing, unordered — team1/team2 are favorite and
            # underdog, so they swap when a line crosses pick'em, and comparing
            # them in order stored the fixture twice with the teams reversed.
            existing = Game.match_existing(week, team1, team2, game_id)
            if existing is not None:
                existing.game_dt = g[6]
                existing.game_id = game_id
                fields = ['game_dt', 'game_id']
                # Points are settled at lock; rewriting them afterwards silently
                # rescores picks members already made.
                if not settings.lock_picks:
                    existing.team1, existing.team2 = team1, team2
                    existing.team1_is_home = g[4]
                    existing.points1 = float(settings.multiplier)
                    existing.points2 = pts2
                    fields += ['team1', 'team2', 'team1_is_home', 'points1', 'points2']
                existing.save(update_fields=fields)
                dupes += 1
                continue

            Game.objects.create(
                team1=team1, team2=team2,
                points1=float(settings.multiplier), points2=pts2,
                team1_is_home=g[4], game_id=game_id, game_dt=g[6],
                # The week that was scraped, not the week the site happens to be
                # showing. These are separate on purpose - `scrape_week` exists so
                # you can pull a week ahead - but the store used settings.week, so
                # scraping week 1 while the site sat on week 2 filed week 1's
                # fixtures under week 2 and left both weeks wrong.
                week=week,
            )
            added += 1
        from django.db.models import Min
        from datetime import timedelta as _td2
        # Only recompute the lock when the week just scraped is the live one.
        # Pulling a future week must not drag the current week's lock onto a
        # kickoff that is still a fortnight away.
        if week == settings.week:
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
                send_picks_published_email(settings)
            except Exception:
                log.exception('manual scrape: picks-live email failed')
            try:
                from .auto import make_bot_picks
                make_bot_picks()
            except Exception:
                log.exception('manual scrape: bot picks failed')
        msg = f'Scraped week {week}: {added} added, {dupes} updated.'
        if skipped_day:
            msg += (f' {skipped_day} skipped - not on a day this league plays.')
        messages.success(request, msg)

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
        # One implementation, shared with the autopilot. This branch carried its
        # own copy of the advance, and it drifted: it never cleared the intro,
        # the grade time or the retry state, so last week's note went out again
        # attached to this week's games.
        from .auto import do_advance_week
        do_advance_week(settings)
        settings.refresh_from_db()
        messages.success(request, f'Advanced to week {settings.week}.')

    elif 'newseason' in request.POST:
        save_form = forms.SaveSeasonForm(request.POST)
        if not save_form.is_valid():
            # The reset used to run whether or not the record was written - and
            # the page posted no year, so it never was. Nothing happens now
            # unless the season can be archived first.
            messages.error(request, 'Season not saved: a year is required. Nothing was reset.')
        else:
            from .seasons import archive_and_reset
            record = archive_and_reset(
                settings, save_form.cleaned_data['year'],
                save_form.cleaned_data.get('notes', ''))
            messages.success(request, f'{record.year} season saved. New season started.')

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

    # Which game-day boxes are ticked. Blank means every day, so show them all
    # ticked rather than none - "no filter" and "no days" would look identical.
    _day_set = settings.scrape_day_set()
    scrape_day_choices = [
        (val, name, (not _day_set) or val in _day_set)
        for val, name in WEEKDAY_NAMES.items()
    ]

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
        'scrape_day_choices': scrape_day_choices,
    })


@staff_member_required
@require_POST
def generate_recap(request):
    from .auto import build_recap
    settings = SiteSettings.get()
    last_week = settings.week - 1
    if last_week < 1:
        return JsonResponse(
            {'error': 'Week 1 has no previous week to recap.'}, status=400)
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
