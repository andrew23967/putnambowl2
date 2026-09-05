"""Who is in which league, and who may manage it.

`current_league(request)` is the only way a request learns its league: the
signed-in user's profile. Superusers may have no league at all - `createsuperuser`
runs before any league exists - and are sent to the site admin instead.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def current_league(request):
    """The league the request belongs to, or None. Memoised on the request."""
    if hasattr(request, 'league'):
        return request.league
    league = None
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        profile = getattr(user, 'profile', None)
        league = profile.league if profile is not None else None
    request.league = league
    return league


def current_settings(request):
    """This league's settings row."""
    from main.models import LeagueSettings
    return LeagueSettings.for_league(current_league(request))


def is_manager(user, league=None):
    """Managers run their own league; superusers may run any."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, 'profile', None)
    if profile is None or profile.role != 'manager':
        return False
    return league is None or profile.league_id == league.id


def league_required(view):
    """Signed in, and in a league. A superuser without one goes to /leagues/."""
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        league = current_league(request)
        if league is None:
            if request.user.is_superuser:
                return redirect('leagues:index')
            raise PermissionDenied('This account is not in a league.')
        if not league.is_active and not request.user.is_superuser:
            raise PermissionDenied('This league is closed.')
        return view(request, *args, **kwargs)
    return wrapper


def league_manager_required(view):
    @wraps(view)
    @league_required
    def wrapper(request, *args, **kwargs):
        if not is_manager(request.user, request.league):
            raise PermissionDenied('Only a league manager can do this.')
        return view(request, *args, **kwargs)
    return wrapper


def superuser_required(view):
    return user_passes_test(
        lambda u: u.is_active and u.is_superuser, login_url='leagues:login')(view)
