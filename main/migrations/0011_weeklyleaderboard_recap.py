from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0010_game_week_remove_history'),
    ]

    operations = [
        migrations.AddField(
            model_name='weeklyleaderboard',
            name='recap',
            field=models.TextField(blank=True, default=''),
        ),
    ]
