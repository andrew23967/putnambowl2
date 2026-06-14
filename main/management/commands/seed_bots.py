"""
python manage.py seed_bots

Creates 15 bot players with randomised retroactive history.
Safe to run multiple times — skips bots that already exist.
"""
import random
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from main.models import History, WeeklyLeaderboard, SiteSettings

BOTS = [
    ('bot_hal',      'HAL 9000',   '#ef4444'),
    ('bot_jarvis',   'JARVIS',     '#3b82f6'),
    ('bot_optimus',  'Optimus',    '#f59e0b'),
    ('bot_walle',    'WALL·E',     '#10b981'),
    ('bot_r2d2',     'R2-D2',      '#6366f1'),
    ('bot_c3po',     'C-3PO',      '#fbbf24'),
    ('bot_bender',   'Bender',     '#8b5cf6'),
    ('bot_ultron',   'Ultron',     '#f87171'),
    ('bot_skynet',   'Skynet',     '#64748b'),
    ('bot_glados',   'GLaDOS',     '#ec4899'),
    ('bot_data',     'Data',       '#06b6d4'),
    ('bot_marvin',   'Marvin',     '#475569'),
    ('bot_ava',      'Ava',        '#a78bfa'),
    ('bot_samantha', 'Samantha',   '#34d399'),
    ('bot_bishop',   'Bishop',     '#94a3b8'),
]


class Command(BaseCommand):
    help = 'Create 15 bot players with random retroactive picks'

    def handle(self, *args, **options):
        random.seed()  # true random, not seeded
        self.stdout.write('Creating bots...')

        bots = []
        for username, real_name, theme in BOTS:
            user, created = User.objects.get_or_create(username=username)
            if created:
                user.set_password('botpassword!')
                user.save()
                self.stdout.write(f'  Created {username}')
            p = user.profile
            p.real_name = real_name
            p.theme = theme
            p.is_bot = True
            p.score = 0
            p.save()
            bots.append(user)

        # Retroactively add bot picks to each history week
        cumulative = {u.username: 0.0 for u in bots}

        for hist in History.objects.order_by('week'):
            modified = False
            for game in hist.games_data:
                if not game.get('graded'):
                    continue
                points1 = game.get('points1', 1.0)
                points2 = game.get('points2', 1.0)
                winner  = game.get('winner', '')
                pp = game.setdefault('player_picks', {})

                for bot in bots:
                    if bot.username in pp:
                        continue  # already has a pick
                    choice  = random.choice(['team1', 'team2'])
                    correct = (choice == winner)
                    pts     = (points1 if choice == 'team1' else points2) if correct else 0.0
                    pp[bot.username] = {
                        'choice': choice,
                        'correct': correct,
                        'points': round(pts, 2),
                        'team_picked': game.get('team1' if choice == 'team1' else 'team2', ''),
                    }
                    cumulative[bot.username] = round(cumulative[bot.username] + pts, 2)
                    modified = True

            if modified:
                hist.save()

            # Rebuild WeeklyLeaderboard entries to include bots
            try:
                lb = WeeklyLeaderboard.objects.get(week=hist.week)
                existing = {e['username']: e['score'] for e in lb.entries}
                for bot in bots:
                    existing[bot.username] = cumulative[bot.username]
                lb.entries = [{'username': k, 'score': v} for k, v in existing.items()]
                lb.save()
            except WeeklyLeaderboard.DoesNotExist:
                pass

            self.stdout.write(f'  Week {hist.week} updated')

        # Set final scores on profiles
        for bot in bots:
            bot.profile.score = cumulative[bot.username]
            bot.profile.save()
            self.stdout.write(f'  {bot.username}: {cumulative[bot.username]} pts')

        self.stdout.write(self.style.SUCCESS('\nBots seeded successfully.'))
