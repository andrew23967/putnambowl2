from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0005_lock_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='auto_tz',
            field=models.CharField(default='UTC', max_length=50),
        ),
    ]
