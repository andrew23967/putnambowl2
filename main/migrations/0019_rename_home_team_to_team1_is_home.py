"""Rename Game.home_team -> Game.team1_is_home.

A rename, deliberately hand-written: `makemigrations` autodetected this as a
RemoveField + AddField pair, which would drop the column and reset every stored
game to the default, losing which side was at home for the whole season's
history. RenameField carries the values across.

The stored values are already correct for the new name — both writers set it from
"team1/the favorite is home". Only the readers disagreed, so no data migration is
needed, just the rename.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0018_processedemail_attempts_processedemail_deferred'),
    ]

    operations = [
        migrations.RenameField(
            model_name='game',
            old_name='home_team',
            new_name='team1_is_home',
        ),
        migrations.AlterField(
            model_name='game',
            name='team1_is_home',
            field=models.BooleanField(
                default=True,
                help_text='True = team1 (the favorite) is the home team'),
        ),
    ]
