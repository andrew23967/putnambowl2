from .models import SiteSettings


def site_settings(request):
    settings = SiteSettings.get()
    return {
        'site_week': settings.week,
        'site_publish': settings.publish,
        'site_edit': settings.edit,
        'site_lock_picks': settings.lock_picks,
    }
