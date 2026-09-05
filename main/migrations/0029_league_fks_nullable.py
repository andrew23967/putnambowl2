"""Add a league to every table that needs one, nullable for now.

Three steps, three migrations: add the column empty (this one), point every
existing row at the default league (0030), then make it required (0031). A
single non-null AddField would need a default, and there is no right default
until the backfill has run.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('main', '0028_rename_sitesettings_leaguesettings'),
        ('leagues', '0002_default_league'),
    ]
    operations = [
        migrations.AddField(
            model_name='leaguesettings', name='league',
            field=models.OneToOneField(
                null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='league_settings', to='leagues.league'),
        ),
        migrations.AddField(
            model_name='game', name='league',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='games', to='leagues.league'),
        ),
        migrations.AddField(
            model_name='weeklyleaderboard', name='league',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='leaderboards', to='leagues.league'),
        ),
        migrations.AddField(
            model_name='leagueemail', name='league',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='emails', to='leagues.league'),
        ),
        migrations.AddField(
            model_name='introtemplate', name='league',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='intro_templates', to='leagues.league'),
        ),
        migrations.AddField(
            model_name='seasonrecord', name='league',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='seasons', to='leagues.league'),
        ),
    ]
