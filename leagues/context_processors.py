from .access import current_league, is_manager


def league(request):
    """`league` and `is_manager` on every template."""
    lg = current_league(request)
    user = getattr(request, 'user', None)
    return {
        'league': lg,
        'is_manager': bool(user is not None and is_manager(user, lg)),
    }
