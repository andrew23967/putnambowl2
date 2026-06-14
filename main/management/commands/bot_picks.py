"""
python manage.py bot_picks

Assigns random picks to all bot players for any current-week games
they haven't picked yet. Run this whenever new games are published.
"""
import random
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from main.models import Game, Pick


class Command(BaseCommand):
    help = 'Assign random picks to all bots for unpicked current games'

    def handle(self, *args, **options):
        random.seed()
        bots = User.objects.filter(profile__is_bot=True)
        if not bots.exists():
            self.stdout.write('No bots found. Run seed_bots first.')
            return

        games = Game.objects.filter(graded=False)
        if not games.exists():
            self.stdout.write('No ungraded games to pick.')
            return

        created_count = 0
        for bot in bots:
            for game in games:
                _, created = Pick.objects.get_or_create(
                    user=bot,
                    game=game,
                    defaults={'choice': random.choice(['team1', 'team2'])},
                )
                if created:
                    created_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'Done — {created_count} picks assigned across {bots.count()} bots.'
        ))
