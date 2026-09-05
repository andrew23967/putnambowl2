import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0008_remove_profile_unread_messages'),
        ('leagues', '0002_default_league'),
    ]
    operations = [
        migrations.AddField(
            model_name='profile', name='league',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='profiles', to='leagues.league'),
        ),
        migrations.AddField(
            model_name='profile', name='role',
            field=models.CharField(
                choices=[('member', 'Member'), ('manager', 'Manager')],
                default='member', max_length=10),
        ),
        migrations.AddField(
            model_name='profile', name='email_weekly',
            field=models.BooleanField(
                default=True, help_text='The weekly picks-are-live email.'),
        ),
        migrations.AddField(
            model_name='profile', name='email_reminder',
            field=models.BooleanField(
                default=True, help_text='The nudge before picks lock.'),
        ),
    ]
