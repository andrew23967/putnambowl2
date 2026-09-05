from django.conf import settings as django_settings

from .access import current_league, is_manager


def league(request):
    """`league`, `is_manager` and `email_paused` on every template."""
    lg = current_league(request)
    user = getattr(request, 'user', None)
    return {
        'league': lg,
        'is_manager': bool(user is not None and is_manager(user, lg)),
        'email_paused': bool(getattr(django_settings, 'EMAIL_PAUSED', False)),
    }
