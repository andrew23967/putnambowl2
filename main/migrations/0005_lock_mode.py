from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0004_tick_and_schedule_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='lock_mode',
            field=models.CharField(default='offset', max_length=10),
        ),
    ]
