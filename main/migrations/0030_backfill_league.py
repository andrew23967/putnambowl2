"""Point every existing row at the default league.

Also re-keys the site's own feed rows: message ids were `<site-recap-2026-w3@…>`
and are now `<site-putnambowl-recap-2026-w3@…>`, so a recap regenerated after
this deploy replaces the row it wrote before rather than adding a second.
"""
from django.db import migrations

PREFIXES = ('<site-recap-', '<site-picks-live-', '<site-reminder-')


def forwards(apps, schema_editor):
    League = apps.get_model('leagues', 'League')
    league = League.objects.get(slug='putnambowl')
    for name in ('LeagueSettings', 'Game', 'WeeklyLeaderboard', 'LeagueEmail',
                 'IntroTemplate', 'SeasonRecord'):
        apps.get_model('main', name).objects.filter(league__isnull=True).update(league=league)

    LeagueEmail = apps.get_model('main', 'LeagueEmail')
    for row in LeagueEmail.objects.filter(source='site'):
        for prefix in PREFIXES:
            if row.message_id.startswith(prefix):
                row.message_id = '<site-putnambowl-' + row.message_id[len('<site-'):]
                row.save(update_fields=['message_id'])
                break


def backwards(apps, schema_editor):
    LeagueEmail = apps.get_model('main', 'LeagueEmail')
    for row in LeagueEmail.objects.filter(message_id__startswith='<site-putnambowl-'):
        row.message_id = '<site-' + row.message_id[len('<site-putnambowl-'):]
        row.save(update_fields=['message_id'])


class Migration(migrations.Migration):
    dependencies = [('main', '0029_league_fks_nullable')]
    operations = [migrations.RunPython(forwards, backwards)]
