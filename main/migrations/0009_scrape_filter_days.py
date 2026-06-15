from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0008_auto_scrape_minute'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='scrape_filter_from_day',
            field=models.IntegerField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='scrape_filter_to_day',
            field=models.IntegerField(null=True, blank=True),
        ),
    ]
