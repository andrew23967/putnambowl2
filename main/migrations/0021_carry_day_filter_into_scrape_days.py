"""Carry the contiguous from/to day filter into the new scrape_days set.

Without this the new field stays blank, which means "every day" — so this league,
configured from_day=6 to_day=6 for a Sunday-only slate, would silently start
pulling Thursday night games the first time the worker ran after deploy.

Reversible: a set folds back to a range when it happens to be contiguous.
"""
from django.db import migrations


def _expand(from_day, to_day):
    """The weekdays a from/to pair covered, wrapping through Sunday."""
    if from_day is None or to_day is None:
        return []
    if from_day <= to_day:
        return list(range(from_day, to_day + 1))
    return list(range(from_day, 7)) + list(range(0, to_day + 1))


def forwards(apps, schema_editor):
    SiteSettings = apps.get_model('main', 'SiteSettings')
    for s in SiteSettings.objects.all():
        if s.scrape_days:
            continue
        days = _expand(s.scrape_filter_from_day, s.scrape_filter_to_day)
        # A full week is the same as no filter; leave it blank so the intent reads.
        if days and len(days) < 7:
            s.scrape_days = ','.join(str(d) for d in days)
            s.save(update_fields=['scrape_days'])


def backwards(apps, schema_editor):
    SiteSettings = apps.get_model('main', 'SiteSettings')
    for s in SiteSettings.objects.all():
        days = sorted(int(d) for d in (s.scrape_days or '').split(',') if d.strip().isdigit())
        if days:
            s.scrape_filter_from_day, s.scrape_filter_to_day = days[0], days[-1]
            s.save(update_fields=['scrape_filter_from_day', 'scrape_filter_to_day'])


class Migration(migrations.Migration):
    dependencies = [('main', '0020_sitesettings_auto_advance_and_more')]
    operations = [migrations.RunPython(forwards, backwards)]
