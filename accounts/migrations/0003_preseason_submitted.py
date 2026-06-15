from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_profile_is_bot'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='preseason_submitted',
            field=models.BooleanField(default=False),
        ),
    ]
