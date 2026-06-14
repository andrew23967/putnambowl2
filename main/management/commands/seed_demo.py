"""
python manage.py seed_demo

Creates 6 fake players and 8 weeks of graded history to demo the UI.
Safe to run multiple times — skips existing users/weeks.
"""
import random
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from main.models import (
    SiteSettings, Game, Pick, History, WeeklyLeaderboard, SeasonRecord, Announcement
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


class Command(BaseCommand):
    help = 'Seed demo data: 6 players + 8 weeks of history'

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
            p.save()
            users.append(user)

        # Running cumulative scores
        cumulative = {u.username: 0.0 for u in users}

        for week_idx, matchups in enumerate(MATCHUPS_BY_WEEK):
            week = week_idx + 1
            if History.objects.filter(week=week).exists():
                self.stdout.write(f'  Week {week} already exists, skipping.')
                # Still rebuild cumulative from stored data
                try:
                    lb = WeeklyLeaderboard.objects.get(week=week)
                    for entry in lb.entries:
                        cumulative[entry['username']] = entry['score']
                except WeeklyLeaderboard.DoesNotExist:
                    pass
                continue

            week_games = []
            for t1i, t2i, dog_ml, fav_ml, dog_pts, fav_pts, winner in matchups:
                team1 = TEAM_VALUES[t1i % len(TEAM_VALUES)]
                team2 = TEAM_VALUES[t2i % len(TEAM_VALUES)]
                game_data = {
                    'team1': team1, 'team2': team2,
                    'points1': dog_pts, 'points2': fav_pts,
                    'winner': winner, 'graded': True,
                    'player_picks': {},
                }
                for user in users:
                    # Simulate realistic pick tendencies
                    bias = 0.55 if random.random() > 0.5 else 0.45
                    choice = 'team1' if random.random() < bias else 'team2'
                    correct = (choice == winner)
                    pts = (dog_pts if choice == 'team1' else fav_pts) if correct else 0
                    game_data['player_picks'][user.username] = {
                        'choice': choice, 'correct': correct,
                        'points': round(pts, 2),
                        'team_picked': team1.split()[-1] if choice == 'team1' else team2.split()[-1],
                    }
                    cumulative[user.username] = round(cumulative[user.username] + pts, 2)
                week_games.append(game_data)

            # Build players_list with cumulative scores
            players_list = sorted(
                [{'username': k, 'score': v} for k, v in cumulative.items()],
                key=lambda x: x['score'], reverse=True
            )

            History.objects.create(
                week=week,
                games_data=week_games,
                players_list=[p['username'] for p in players_list],
            )
            WeeklyLeaderboard.objects.update_or_create(
                week=week,
                defaults={'entries': players_list}
            )
            self.stdout.write(f'  Created week {week} history')

        # Set site to week 9 (current week, not yet graded)
        settings = SiteSettings.get()
        settings.week = 9
        settings.publish = True
        settings.edit = False
        settings.lock_picks = True
        settings.save()

        # Update player scores to match end of week 8
        for user in users:
            user.profile.score = cumulative[user.username]
            user.profile.save()
            self.stdout.write(f'  {user.username}: {cumulative[user.username]} pts')

        # Add a couple of current week games (ungraded)
        if not Game.objects.exists():
            for t1i, t2i, dog_ml, fav_ml, dog_pts, fav_pts, _ in MATCHUPS_BY_WEEK[0][:3]:
                team1 = TEAM_VALUES[t1i % len(TEAM_VALUES)]
                team2 = TEAM_VALUES[t2i % len(TEAM_VALUES)]
                game = Game.objects.create(
                    team1=team1, team2=team2,
                    points1=dog_pts, points2=fav_pts,
                    graded=False, winner='',
                    date='Sun 4:25 PM',
                )
                # Give each user a pick
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
        Announcement.objects.get_or_create(message='Welcome to PutnamBowl Week 9! Picks are locked. Good luck!')

        self.stdout.write(self.style.SUCCESS('\nDemo data seeded! Login with any username and password "password123".'))
        self.stdout.write('Users: ' + ', '.join(p[0] for p in PLAYERS))
