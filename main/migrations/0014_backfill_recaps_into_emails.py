"""Carry existing PutnamBot recaps into the Emails feed.

The feed reads only LeagueEmail, so without this every recap written before the
feed existed would vanish from the site — the archive rows would still be in
WeeklyLeaderboard, but nothing would render them.

Recaps have no timestamp of their own, so each one is dated from the last kickoff
of the week it covers: a recap is written once that week's games are done, which
makes the final kickoff the closest true anchor available. Weeks with no stored
kickoffs fall back to the migration time, ordered by week so the feed still reads
in the right sequence.
"""
from datetime import timedelta

from django.db import migrations
from django.utils import timezone


SIGNOFF = (
    "——\n"
    "I'm PutnamBot, the AI commissioner of this league. This recap is mine — "
    "I write one after every week is scored."
)


def forwards(apps, schema_editor):
    WeeklyLeaderboard = apps.get_model('main', 'WeeklyLeaderboard')
    LeagueEmail = apps.get_model('main', 'LeagueEmail')
    Game = apps.get_model('main', 'Game')
    User = apps.get_model('auth', 'User')

    bot = User.objects.filter(username='putnambot').first()
    now = timezone.now()

    for lb in WeeklyLeaderboard.objects.exclude(recap='').order_by('week'):
        kickoffs = list(
            Game.objects.filter(week=lb.week, game_dt__isnull=False)
            .order_by('-game_dt').values_list('game_dt', flat=True)[:1]
        )
        # Ordering by week keeps the fallback dates in sequence rather than
        # collapsing every undated recap onto the same instant.
        sent_at = kickoffs[0] if kickoffs else now + timedelta(seconds=lb.week)

        LeagueEmail.objects.update_or_create(
            message_id=f'<recap-w{lb.week}@putnambowl.backfill>',
            defaults={
                'author': bot,
                'from_name': 'putnambot',
                'subject': f'Week {lb.week} recap',
                'body': f'{lb.recap.strip()}\n\n{SIGNOFF}',
                'source': 'site',
                'sent_at': sent_at,
                'recipient_count': 0,
                'published': True,
            },
        )


def backwards(apps, schema_editor):
    LeagueEmail = apps.get_model('main', 'LeagueEmail')
    LeagueEmail.objects.filter(message_id__endswith='@putnambowl.backfill>').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0013_leagueemail_delete_announcement_and_more'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
