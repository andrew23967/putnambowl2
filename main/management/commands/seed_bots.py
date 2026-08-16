"""
python manage.py seed_bots

Creates 15 bot players and back-fills them into every completed week.
Safe to run multiple times — skips bots and picks that already exist.
"""
import random
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from main.models import Game, Pick, WeeklyLeaderboard, SiteSettings

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
            p.preseason_submitted = True
            p.save()
            bots.append(user)

        current_week = SiteSettings.get().week
        cumulative = {u.username: 0.0 for u in bots}

        # Which (bot, game) pairs already have a pick, so reruns are no-ops.
        bot_ids = [b.id for b in bots]
        existing = set(
            Pick.objects.filter(user_id__in=bot_ids).values_list('user_id', 'game_id')
        )

        games_by_week = defaultdict(list)
        for game in Game.objects.filter(week__lt=current_week, graded=True):
            games_by_week[game.week].append(game)

        for week in sorted(games_by_week):
            new_picks = []
            for game in games_by_week[week]:
                for bot in bots:
                    if (bot.id, game.id) in existing:
                        # Already picked — still count it toward the running total.
                        continue
                    choice = random.choice(['team1', 'team2'])
                    new_picks.append(Pick(user=bot, game=game, choice=choice))
                    if choice == game.winner:
                        pts = game.points1 if choice == 'team1' else game.points2
                        cumulative[bot.username] = round(cumulative[bot.username] + pts, 2)
            Pick.objects.bulk_create(new_picks, batch_size=500)

            # Rebuild WeeklyLeaderboard entries to include bots
            try:
                lb = WeeklyLeaderboard.objects.get(week=week)
                entries = {e['username']: e['score'] for e in lb.entries}
                for bot in bots:
                    entries[bot.username] = cumulative[bot.username]
                lb.entries = [{'username': k, 'score': v} for k, v in entries.items()]
                lb.save()
            except WeeklyLeaderboard.DoesNotExist:
                pass

            self.stdout.write(f'  Week {week} updated ({len(new_picks)} picks added)')

        # Set final scores on profiles
        for bot in bots:
            bot.profile.score = cumulative[bot.username]
            bot.profile.save()
            self.stdout.write(f'  {bot.username}: {cumulative[bot.username]} pts')

        self.stdout.write(self.style.SUCCESS('\nBots seeded successfully.'))
