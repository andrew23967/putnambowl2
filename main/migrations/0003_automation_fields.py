from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0002_weekly_recap'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='auto_enabled',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='auto_scrape_weekday',
            field=models.IntegerField(default=1),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='auto_scrape_hour',
            field=models.IntegerField(default=9),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='auto_lock_offset_minutes',
            field=models.IntegerField(default=10),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='first_game_dt',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
