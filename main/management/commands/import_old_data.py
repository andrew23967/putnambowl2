"""
Import data from the old PutnamBowl SQLite database into this new app.

Usage:
    # see what would happen, change nothing
    python manage.py import_old_data --db path/to/old/db.sqlite3 --users-only --dry-run

    # move the league's accounts across for a fresh season
    python manage.py import_old_data --db path/to/old/db.sqlite3 --users-only --zero-scores

Password hashes are copied verbatim, so existing passwords keep working.
Existing usernames are never overwritten.
"""
import sqlite3
import json
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth.models import User
from main.models import Game, Pick, WeeklyLeaderboard, SeasonRecord
from main.history_import import normalise_game


class Command(BaseCommand):
    help = 'Import users, history, and leaderboards from old PutnamBowl database'

    def add_arguments(self, parser):
        parser.add_argument('--db', required=True, help='Path to old db.sqlite3')
        parser.add_argument(
            '--users-only', action='store_true',
            help='Import only accounts and profiles — skip history, leaderboards, announcements.',
        )
        parser.add_argument(
            '--zero-scores', action='store_true',
            help="Start imported players at 0 points. Use when the target site is "
                 "beginning a new season; otherwise last season's totals carry over.",
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would be imported and roll back without writing.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        try:
            with transaction.atomic():
                self._run(options)
                if dry_run:
                    self.stdout.write(self.style.WARNING('\nDRY RUN — rolling back, nothing written.'))
                    raise _Rollback()
        except _Rollback:
            return
        self.stdout.write(self.style.SUCCESS('\nImport complete!'))

    def _run(self, options):
        users_only = options['users_only']
        zero_scores = options['zero_scores']

        conn = sqlite3.connect(options['db'])
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Import users + profiles
        cur.execute("SELECT * FROM auth_user")
        old_users = cur.fetchall()
        self.stdout.write(f'Found {len(old_users)} users in the source database')

        imported = skipped = 0
        for row in old_users:
            if User.objects.filter(username=row['username']).exists():
                self.stdout.write(f'  Skipping existing user: {row["username"]}')
                skipped += 1
                continue
            user = User(
                username=row['username'],
                email=row['email'] or '',
                is_staff=row['is_staff'],
                is_superuser=row['is_superuser'],
                is_active=row['is_active'],
                date_joined=row['date_joined'],
            )
            # Assign the hash directly rather than set_password, so members keep
            # the password they already use.
            user.password = row['password']
            user.save()
            self.stdout.write(f'  Imported user: {user.username} <{user.email}>')
            imported += 1

        self.stdout.write(f'Users: {imported} imported, {skipped} skipped')

        # Import profile preferences (and optionally scores)
        cur.execute("SELECT * FROM main_profile")
        profiles = cur.fetchall()
        usernames = {r['id']: r['username'] for r in old_users}
        for row in profiles:
            username = usernames.get(row['user_id'])
            if not username:
                continue
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                continue
            p = user.profile
            p.score = 0 if zero_scores else (row['score'] or 0)
            p.bio = row['bio'] or ''
            p.real_name = row['real_name'] or ''
            p.theme = row['theme'] or '#00897b'
            p.favorite_team = row['favorite_team'] or 'Arizona Cardinals'
            p.big_loser = row['big_loser'] or 'Arizona Cardinals'
            p.nfc_champ = row['nfc_champ'] or 'Arizona Cardinals'
            p.afc_champ = row['afc_champ'] or 'Buffalo Bills'
            p.superbowl_winner = row['superbowl_winner'] or 'Arizona Cardinals'
            p.save()
            self.stdout.write(f'  Profile: {user.username} (score={p.score})')

        if users_only:
            self.stdout.write('--users-only: skipping history, leaderboards, announcements.')
            conn.close()
            return

        # Import history as real Game/Pick rows (the History model is gone —
        # see migration 0010). Kickoff times, ESPN ids and home/away are not in
        # the archive, so those stay at their defaults.
        user_ids = dict(User.objects.values_list('username', 'pk'))
        cur.execute("SELECT * FROM main_history ORDER BY week")
        histories = cur.fetchall()
        for row in histories:
            week = row['week']
            if Game.objects.filter(week=week).exists():
                self.stdout.write(f'  Week {week} already has games, skipping.')
                continue
            try:
                games_data = json.loads(row['games_data']) if row['games_data'] else []
            except (TypeError, ValueError) as e:
                self.stdout.write(f'  Error reading history week {week}: {e}')
                continue

            new_picks = []
            for g in games_data:
                if not isinstance(g, dict):
                    continue
                team1, team2, points1, points2, winner, picks = normalise_game(g)
                if not team1 or not team2:
                    continue
                game = Game.objects.create(
                    team1=team1, team2=team2,
                    points1=points1, points2=points2,
                    winner=winner, graded=bool(winner),
                    week=week,
                )
                for username, choice in picks.items():
                    uid = user_ids.get(username)
                    if uid is not None:
                        new_picks.append(Pick(user_id=uid, game_id=game.pk, choice=choice))
            Pick.objects.bulk_create(new_picks, batch_size=500)
            self.stdout.write(f'  Imported history week {week} ({len(new_picks)} picks)')

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

        # The old site's announcements are deliberately not imported: the site
        # has no announcement model any more, and that content is superseded by
        # the Emails feed.

        conn.close()


class _Rollback(Exception):
    """Raised to unwind the transaction at the end of a --dry-run."""
