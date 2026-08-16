"""
Create (or update) the `putnambot` league entry — a bot whose picks come from
Gemini rather than a coin flip.

    python manage.py create_putnambot

Safe to re-run; it updates the existing account rather than duplicating it.
"""
import secrets

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

USERNAME = 'putnambot'

# Matches the voice of the other strategy accounts (mrfavorite, mrunderdog,
# mrrandom), which all explain their method in one sentence.
BIO = ("This account asks an AI (Google's Gemini) to pick every game so you can "
       "see how your results compare to this simple strategy.")


class Command(BaseCommand):
    help = 'Create the PutnamBot AI player'

    def add_arguments(self, parser):
        parser.add_argument('--theme', default='#7c5cff', help='Accent colour')
        parser.add_argument('--favorite-team', default='Detroit Lions')

    def handle(self, *args, **options):
        user, created = User.objects.get_or_create(username=USERNAME)
        if created:
            # No one logs in as PutnamBot; give it an unusable random password.
            user.set_password(secrets.token_urlsafe(32))
        user.is_staff = False
        user.is_superuser = False
        user.is_active = True
        # Deliberately no email: send_picks_published_email() skips bots, but an
        # empty address means it can never be mailed even if that changes.
        user.email = ''
        user.save()

        p = user.profile
        p.real_name = 'PutnamBot'
        p.bio = BIO
        p.theme = options['theme']
        p.favorite_team = options['favorite_team']
        p.is_bot = True
        p.bot_strategy = 'gemini'
        # Only consulted if Gemini is unreachable — a 50/50 coin flip fallback.
        p.bot_underdog_pct = 50
        # Skip the preseason gate; nothing would fill that form in.
        p.preseason_submitted = True
        p.save()

        verb = 'Created' if created else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {USERNAME} (strategy={p.bot_strategy}, theme={p.theme})'
        ))
        self.stdout.write(f'  bio: {p.bio}')
