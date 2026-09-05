from django.conf import settings as django_settings

from .access import current_league, is_manager


def league(request):
    """`league`, `is_manager`, `site_week` and `email_paused` on every template.

    `site_week` is the nav's "W7" badge. One small query per page, and only
    for signed-in members of a league.
    """
    lg = current_league(request)
    user = getattr(request, 'user', None)
    week = None
    if lg is not None:
        from main.models import LeagueSettings
        week = LeagueSettings.for_league(lg).week
    return {
        'league': lg,
        'is_manager': bool(user is not None and is_manager(user, lg)),
        'site_week': week,
        'email_paused': bool(getattr(django_settings, 'EMAIL_PAUSED', False)),
    }
