from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0003_automation_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='tick_interval',
            field=models.IntegerField(default=300),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='auto_scrape_dt',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='auto_lock_dt',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
