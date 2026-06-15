from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0006_auto_tz'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='game',
            name='date',
        ),
        migrations.AddField(
            model_name='game',
            name='game_dt',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
