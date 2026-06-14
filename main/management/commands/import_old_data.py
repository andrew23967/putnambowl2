"""
Import data from the old PutnamBowl SQLite database into this new app.

Usage:
    python manage.py import_old_data --db path/to/old/db.sqlite3
"""
import sqlite3
import json
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from accounts.models import Profile
from main.models import History, WeeklyLeaderboard, SeasonRecord, Announcement


class Command(BaseCommand):
    help = 'Import users, history, and leaderboards from old PutnamBowl database'

    def add_arguments(self, parser):
        parser.add_argument('--db', required=True, help='Path to old db.sqlite3')

    def handle(self, *args, **options):
        conn = sqlite3.connect(options['db'])
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Import users + profiles
        cur.execute("SELECT * FROM auth_user")
        old_users = cur.fetchall()
        self.stdout.write(f'Found {len(old_users)} users')

        for row in old_users:
            if User.objects.filter(username=row['username']).exists():
                self.stdout.write(f'  Skipping existing user: {row["username"]}')
                continue
            user = User(
                username=row['username'],
                email=row['email'] or '',
                is_staff=row['is_staff'],
                is_superuser=row['is_superuser'],
                is_active=row['is_active'],
                date_joined=row['date_joined'],
            )
            user.password = row['password']
            user.save()
            self.stdout.write(f'  Imported user: {user.username}')

        # Import profile scores + preseason picks
        cur.execute("SELECT * FROM main_profile")
        profiles = cur.fetchall()
        for row in profiles:
            cur.execute("SELECT username FROM auth_user WHERE id=?", (row['user_id'],))
            user_row = cur.fetchone()
            if not user_row:
                continue
            try:
                user = User.objects.get(username=user_row['username'])
                p = user.profile
                p.score = row['score'] or 0
                p.bio = row['bio'] or ''
                p.real_name = row['real_name'] or ''
                p.theme = row['theme'] or '#00897b'
                p.favorite_team = row['favorite_team'] or 'Arizona Cardinals'
                p.big_loser = row['big_loser'] or 'Arizona Cardinals'
                p.nfc_champ = row['nfc_champ'] or 'Arizona Cardinals'
                p.afc_champ = row['afc_champ'] or 'Buffalo Bills'
                p.superbowl_winner = row['superbowl_winner'] or 'Arizona Cardinals'
                p.save()
                self.stdout.write(f'  Updated profile: {user.username} (score={p.score})')
            except User.DoesNotExist:
                pass

        # Import history
        cur.execute("SELECT * FROM main_history ORDER BY week")
        histories = cur.fetchall()
        for row in histories:
            try:
                games_data = json.loads(row['games_data']) if row['games_data'] else []
                players_list = json.loads(row['players_list']) if row['players_list'] else []
                History.objects.update_or_create(
                    week=row['week'],
                    defaults={'games_data': games_data, 'players_list': players_list}
                )
                self.stdout.write(f'  Imported history week {row["week"]}')
            except Exception as e:
                self.stdout.write(f'  Error importing history week {row["week"]}: {e}')

        # Import leaderboards
        cur.execute("SELECT * FROM main_leaderboard")
        leaderboards = cur.fetchall()
        for idx, row in enumerate(leaderboards):
            week = idx + 1
            entries = []
            if row['l'] and row['l'] != 'no data':
                for entry in row['l'].split('|'):
                    parts = entry.split(',')
                    if len(parts) >= 2:
                        try:
                            entries.append({'username': parts[1], 'score': float(parts[0])})
                        except ValueError:
                            pass
            WeeklyLeaderboard.objects.update_or_create(
                week=week, defaults={'entries': entries}
            )
            self.stdout.write(f'  Imported leaderboard week {week}')

        # Import announcements
        cur.execute("SELECT * FROM main_announcement")
        for row in cur.fetchall():
            Announcement.objects.get_or_create(message=row['message'])

        conn.close()
        self.stdout.write(self.style.SUCCESS('Import complete!'))
