"""Make the league required, and move the per-league uniqueness with it.

`WeeklyLeaderboard.week` and `IntroTemplate.name` were unique across the site;
two leagues both have a week 5 and a "Standard week" intro, so the uniqueness
is now on (league, week) and (league, name). `SeasonRecord` gains
(league, year) at the same time.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('main', '0030_backfill_league')]
    operations = [
        migrations.AlterField(
            model_name='leaguesettings', name='league',
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='league_settings', to='leagues.league'),
        ),
        migrations.AlterField(
            model_name='game', name='league',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='games', to='leagues.league'),
        ),
        migrations.AlterField(
            model_name='weeklyleaderboard', name='league',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='leaderboards', to='leagues.league'),
        ),
        migrations.AlterField(
            model_name='leagueemail', name='league',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='emails', to='leagues.league'),
        ),
        migrations.AlterField(
            model_name='introtemplate', name='league',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='intro_templates', to='leagues.league'),
        ),
        migrations.AlterField(
            model_name='seasonrecord', name='league',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='seasons', to='leagues.league'),
        ),
        migrations.AlterField(
            model_name='weeklyleaderboard', name='week',
            field=models.IntegerField(default=1),
        ),
        migrations.AlterField(
            model_name='introtemplate', name='name',
            field=models.CharField(max_length=60),
        ),
        migrations.AlterUniqueTogether(
            name='weeklyleaderboard', unique_together={('league', 'week')}),
        migrations.AlterUniqueTogether(
            name='introtemplate', unique_together={('league', 'name')}),
        migrations.AlterUniqueTogether(
            name='seasonrecord', unique_together={('league', 'year')}),
    ]
