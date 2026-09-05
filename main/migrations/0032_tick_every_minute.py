from django.db import migrations, models


def every_minute(apps, schema_editor):
    LeagueSettings = apps.get_model('main', 'LeagueSettings')
    LeagueSettings.objects.update(tick_interval=60)


class Migration(migrations.Migration):
    """The worker ticks every minute. The interval and the retry window came off
    the dashboard; both stay as fields, editable in the Django admin."""

    dependencies = [
        ('main', '0031_league_fks_required'),
    ]

    operations = [
        migrations.AlterField(
            model_name='leaguesettings',
            name='tick_interval',
            field=models.IntegerField(default=60),
        ),
        migrations.RunPython(every_minute, migrations.RunPython.noop),
    ]
