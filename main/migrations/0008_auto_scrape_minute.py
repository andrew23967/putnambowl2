from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0007_game_dt'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='auto_scrape_minute',
            field=models.IntegerField(default=0),
        ),
    ]
