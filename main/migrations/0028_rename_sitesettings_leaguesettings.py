"""SiteSettings -> LeagueSettings.

Hand-written. `makemigrations` autodetects a rename as DeleteModel + CreateModel,
which drops the table and with it the only settings row the site has - the
same trap `0019` documents for the `home_team` rename.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('main', '0027_remove_dead_models_add_season_archive')]
    operations = [
        migrations.RenameModel(old_name='SiteSettings', new_name='LeagueSettings'),
        migrations.AlterModelOptions(
            name='leaguesettings',
            options={'verbose_name': 'League settings',
                     'verbose_name_plural': 'League settings'},
        ),
    ]
