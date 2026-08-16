"""Split the single `grade_api` setting into `scrape_api` + `grade_api`.

One setting used to drive both jobs, which made the useful combination
impossible: nfl-data-py is the only source carrying moneylines (so it must do
the scraping) while ESPN is the only one with live scores (so it should do the
grading).

This migration is deliberately behaviour-neutral — `scrape_api` is backfilled
from whatever `grade_api` already was, so an existing site keeps doing exactly
what it did until someone changes it in the dashboard.
"""
from django.db import migrations, models


def backfill_scrape_api(apps, schema_editor):
    SiteSettings = apps.get_model('main', 'SiteSettings')
    for s in SiteSettings.objects.all():
        s.scrape_api = s.grade_api
        s.save(update_fields=['scrape_api'])


def noop(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0011_weeklyleaderboard_recap'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='scrape_api',
            field=models.CharField(default='nfl_data_py', max_length=20),
        ),
        migrations.RunPython(backfill_scrape_api, noop),
    ]
