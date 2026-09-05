from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .forms import RegisterForm, ProfileForm


def register(request, code=None):
    """Create an account in the league whose join code was given.

    `/join/<code>/` pre-fills the code so an invite link needs no typing.
    """
    from leagues.models import League

    if request.user.is_authenticated:
        return redirect('main:home')
    initial = {'join_code': code} if code else None
    form = RegisterForm(request.POST or None, initial=initial)
    if form.is_valid():
        user = form.save()
        user.profile.league = form.league
        user.profile.role = 'member'
        user.profile.save(update_fields=['league', 'role'])
        login(request, user)
        return redirect('main:home')
    join_league = League.objects.filter(join_code=code, is_active=True).first() if code else None
    return render(request, 'accounts/register.html', {
        'form': form, 'join_code': code or '', 'join_league': join_league,
    })


def _safe_next(request):
    from django.utils.http import url_has_allowed_host_and_scheme
    nxt = request.POST.get('next') or request.GET.get('next') or ''
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return None


def _after_login(request, user):
    # A superuser with no league of their own belongs on the site admin.
    profile = getattr(user, 'profile', None)
    if user.is_superuser and (profile is None or profile.league_id is None):
        return redirect('leagues:index')
    return redirect(_safe_next(request) or 'main:home')


def login_view(request):
    if request.user.is_authenticated:
        return _after_login(request, request.user)
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        login(request, user)
        return _after_login(request, user)
    return render(request, 'accounts/login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('accounts:login')


@login_required
def user_profile(request):
    from leagues.access import current_league, current_settings
    from main import seasons as seasons_mod
    from main.models import Pick, WeeklyLeaderboard
    from main.views import standings_rows

    league = current_league(request)
    if league is None:
        return redirect('leagues:index')
    form = ProfileForm(request.user, request.POST or None)
    if form.is_valid():
        p = request.user.profile
        p.real_name = form.cleaned_data['real_name']
        request.user.email = form.cleaned_data['email']
        p.favorite_team = form.cleaned_data['favorite_team']
        p.bio = form.cleaned_data['bio']
        p.email_weekly = form.cleaned_data['email_weekly']
        p.email_reminder = form.cleaned_data['email_reminder']
        # Both rows, explicitly. A post_save signal used to re-save the profile
        # whenever the user saved, which hid every place that forgot to.
        request.user.save()
        p.save()
        messages.success(request, 'Profile saved.')
        return redirect('accounts:user_profile')

    settings = current_settings(request)
    me = request.user.username
    rows, _ = standings_rows(league, settings, me)
    my_row = next((r for r in rows if r['me']), None)
    graded_picks = list(Pick.objects.filter(user=request.user, game__league=league, game__graded=True)
                        .select_related('game'))

    # Best week from the leaderboard snapshots: entry k is the table going into
    # week k, so week k's gain is entry k+1 minus entry k (live score for the
    # most recent completed week).
    tables = {lb.week: {e['username']: e['score'] for e in lb.entries}
              for lb in WeeklyLeaderboard.objects.filter(league=league)}
    best_week = None
    for k in sorted(tables):
        before = tables[k].get(me)
        after_table = tables.get(k + 1)
        after = after_table.get(me) if after_table else (
            round(request.user.profile.score, 1) if k == settings.week - 1 else None)
        if before is None or after is None:
            continue
        gain = round(after - before, 1)
        if best_week is None or gain > best_week[0]:
            best_week = (gain, k)

    finishes = [dict(f, rank_label=_ordinal(f['rank']))
                for f in seasons_mod.finishes_by_username(league).get(me, [])]
    return render(request, 'accounts/user_profile.html', {
        'form': form,
        'season': {
            'points': request.user.profile.score_display,
            'rank': f"{_ordinal(my_row['rank'])} of {len(rows)}" if my_row else '—',
            'record': f"{sum(1 for p in graded_picks if p.is_correct)}/{len(graded_picks)}",
            'best_week': f'+{best_week[0]} (wk {best_week[1]})' if best_week else '',
        },
        'finishes': finishes,
        'preseason_open': settings.week == 1 and not settings.lock_picks,
    })


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{suffix}'
