"""
python manage.py seed_demo

Creates 6 fake players and 8 completed weeks to demo the UI.
Safe to run multiple times — skips weeks that already have games.
"""
import random
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from main.models import (
    SiteSettings, Game, Pick, WeeklyLeaderboard, SeasonRecord, Announcement
)
from main.teams import TEAMS


PLAYERS = [
    ('jake',   'Jake',   '#6366f1'),
    ('mia',    'Mia',    '#10b981'),
    ('derek',  'Derek',  '#f59e0b'),
    ('sam',    'Sam',    '#ef4444'),
    ('riley',  'Riley',  '#8b5cf6'),
    ('chris',  'Chris',  '#06b6d4'),
]

MATCHUPS_BY_WEEK = [
    # (underdog, favorite, dog_ml, fav_ml, dog_pts, fav_pts, winner)
    [(0, 1, 155, -175, 2.1, 1.0, 'team1'), (2, 3, 130, -150, 1.8, 1.0, 'team2'), (4, 5, 200, -240, 2.8, 1.0, 'team2'), (6, 7, 110, -130, 1.5, 1.0, 'team1')],
    [(8, 9, 145, -165, 2.0, 1.0, 'team2'), (10,11, 120, -140, 1.7, 1.0, 'team1'), (12,13, 180, -210, 2.5, 1.0, 'team1'), (14,15, 105, -125, 1.4, 1.0, 'team2')],
    [(16,17, 160, -185, 2.2, 1.0, 'team1'), (18,19, 140, -160, 1.9, 1.0, 'team2'), (20,21, 115, -135, 1.6, 1.0, 'team1'), (22,23, 190, -220, 2.6, 1.0, 'team2')],
    [(24,25, 135, -155, 1.85, 1.0, 'team2'), (26,27, 150, -170, 2.05, 1.0, 'team1'), (28,29, 125, -145, 1.75, 1.0, 'team1'), (30,31, 170, -195, 2.3, 1.0, 'team2')],
    [(0, 2, 145, -165, 2.0, 1.0, 'team1'), (1, 3, 130, -150, 1.8, 1.0, 'team1'), (4, 6, 110, -130, 1.5, 1.0, 'team2'), (5, 7, 160, -185, 2.2, 1.0, 'team1')],
    [(8,10, 120, -140, 1.7, 1.0, 'team2'), (9,11, 175, -200, 2.4, 1.0, 'team1'), (12,14, 105, -125, 1.4, 1.0, 'team2'), (13,15, 140, -160, 1.9, 1.0, 'team1')],
    [(16,18, 135, -155, 1.85, 1.0, 'team2'), (17,19, 155, -175, 2.1, 1.0, 'team1'), (20,22, 125, -145, 1.75, 1.0, 'team1'), (21,23, 165, -190, 2.25, 1.0, 'team2')],
    [(24,26, 150, -170, 2.05, 1.0, 'team2'), (25,27, 145, -165, 2.0, 1.0, 'team1'), (28,30, 115, -135, 1.6, 1.0, 'team2'), (29,31, 130, -150, 1.8, 1.0, 'team1')],
]

TEAM_VALUES = [t[0] for t in TEAMS]
CURRENT_WEEK = len(MATCHUPS_BY_WEEK) + 1


class Command(BaseCommand):
    help = 'Seed demo data: 6 players + 8 completed weeks'

    def handle(self, *args, **options):
        self.stdout.write('Seeding demo data...')
        random.seed(42)

        # Create players
        users = []
        for username, real_name, theme in PLAYERS:
            user, created = User.objects.get_or_create(username=username)
            if created:
                user.set_password('password123')
                user.save()
                self.stdout.write(f'  Created user: {username}')
            p = user.profile
            p.real_name = real_name
            p.theme = theme
            p.score = 0
            p.preseason_submitted = True
            p.save()
            users.append(user)

        # Running cumulative scores
        cumulative = {u.username: 0.0 for u in users}

        for week_idx, matchups in enumerate(MATCHUPS_BY_WEEK):
            week = week_idx + 1

            # WeeklyLeaderboard(week=N) holds the scores as they stood BEFORE
            # week N was scored, so snapshot before adding this week's points.
            WeeklyLeaderboard.objects.update_or_create(
                week=week,
                defaults={'entries': sorted(
                    [{'username': k, 'score': v} for k, v in cumulative.items()],
                    key=lambda x: x['score'], reverse=True
                )},
            )

            if Game.objects.filter(week=week).exists():
                self.stdout.write(f'  Week {week} already exists, skipping.')
                # Replay the stored picks so cumulative stays accurate.
                for pick in Pick.objects.filter(game__week=week).select_related('game', 'user'):
                    if pick.user.username in cumulative and pick.is_correct:
                        cumulative[pick.user.username] = round(
                            cumulative[pick.user.username] + pick.points_earned, 2
                        )
                continue

            for t1i, t2i, dog_ml, fav_ml, dog_pts, fav_pts, winner in matchups:
                game = Game.objects.create(
                    team1=TEAM_VALUES[t1i % len(TEAM_VALUES)],
                    team2=TEAM_VALUES[t2i % len(TEAM_VALUES)],
                    points1=dog_pts, points2=fav_pts,
                    winner=winner, graded=True,
                    week=week,
                )
                for user in users:
                    # Simulate realistic pick tendencies
                    bias = 0.55 if random.random() > 0.5 else 0.45
                    choice = 'team1' if random.random() < bias else 'team2'
                    Pick.objects.create(user=user, game=game, choice=choice)
                    if choice == winner:
                        pts = dog_pts if choice == 'team1' else fav_pts
                        cumulative[user.username] = round(cumulative[user.username] + pts, 2)

            self.stdout.write(f'  Created week {week}')

        # Site sits on the week after the last completed one, picks locked.
        settings = SiteSettings.get()
        settings.week = CURRENT_WEEK
        settings.scrape_week = CURRENT_WEEK
        settings.publish = True
        settings.edit = False
        settings.lock_picks = True
        settings.save()

        # Update player scores to match end of the last completed week
        for user in users:
            user.profile.score = cumulative[user.username]
            user.profile.save()
            self.stdout.write(f'  {user.username}: {cumulative[user.username]} pts')

        # Add a few current-week games (ungraded)
        if not Game.objects.filter(week=CURRENT_WEEK).exists():
            for t1i, t2i, dog_ml, fav_ml, dog_pts, fav_pts, _ in MATCHUPS_BY_WEEK[0][:3]:
                game = Game.objects.create(
                    team1=TEAM_VALUES[t1i % len(TEAM_VALUES)],
                    team2=TEAM_VALUES[t2i % len(TEAM_VALUES)],
                    points1=dog_pts, points2=fav_pts,
                    graded=False, winner='',
                    week=CURRENT_WEEK,
                )
                for user in users:
                    choice = 'team1' if random.random() < 0.5 else 'team2'
                    Pick.objects.get_or_create(user=user, game=game, defaults={'choice': choice})

        # Add a past season record
        SeasonRecord.objects.get_or_create(
            year=2024,
            defaults={
                'winner_username': PLAYERS[0][0],
                'final_standings': [
                    {'username': p[0], 'score': round(cumulative[p[0]] * 0.9 + random.uniform(-5, 5), 1)}
                    for p in PLAYERS
                ],
                'notes': '2024 Season — incredible finish, jake won it all on week 18!',
            }
        )

        # Announcement
        Announcement.objects.get_or_create(
            message=f'Welcome to PutnamBowl Week {CURRENT_WEEK}! Picks are locked. Good luck!'
        )

        self.stdout.write(self.style.SUCCESS('\nDemo data seeded! Login with any username and password "password123".'))
        self.stdout.write('Users: ' + ', '.join(p[0] for p in PLAYERS))
