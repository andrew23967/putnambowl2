import time
import random
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Rapid-fire test of auto-pilot using historical data (default: 2024, weeks 1-3, 10s delays).'

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, default=2024)
        parser.add_argument('--start-week', type=int, default=1)
        parser.add_argument('--end-week', type=int, default=3)
        parser.add_argument('--lock-delay', type=int, default=10, help='Seconds before locking picks')
        parser.add_argument('--grade-delay', type=int, default=10, help='Seconds before grading')

    def handle(self, *args, **options):
        from django.contrib.auth.models import User
        from main.models import SiteSettings, Game, Pick
        from main.auto import do_scrape_and_publish, do_lock_picks, do_grade, do_advance_week

        year = options['year']
        start_week = options['start_week']
        end_week = options['end_week']
        lock_delay = options['lock_delay']
        grade_delay = options['grade_delay']

        settings = SiteSettings.get()

        # Clean slate — clear any existing games/picks and set starting week
        self.stdout.write(f'[test_auto] Resetting to week {start_week} ({year})')
        Pick.objects.all().delete()
        Game.objects.all().delete()
        settings.week = start_week
        settings.scrape_week = start_week
        settings.publish = False
        settings.lock_picks = False
        settings.edit = True
        settings.grade_api = 'espn'
        settings.save()

        for week in range(start_week, end_week + 1):
            settings.refresh_from_db()
            self.stdout.write(f'\n=== Week {week} ({year}) ===')

            # 1. Scrape + publish
            self.stdout.write('  Scraping...')
            added = do_scrape_and_publish(settings, year=year)
            self.stdout.write(f'  {added} games added.')

            # Random picks for all players
            players = User.objects.filter(is_active=True)
            games = list(Game.objects.all())
            pick_count = 0
            for player in players:
                for game in games:
                    choice = random.choice(['team1', 'team2'])
                    Pick.objects.get_or_create(user=player, game=game, defaults={'choice': choice})
                    pick_count += 1
            self.stdout.write(f'  {pick_count} random picks made. Waiting {lock_delay}s to lock...')

            # 2. Lock picks
            time.sleep(lock_delay)
            settings.refresh_from_db()
            do_lock_picks(settings)
            self.stdout.write(f'  Picks locked. Waiting {grade_delay}s to grade...')

            # 3. Grade
            time.sleep(grade_delay)
            settings.refresh_from_db()
            graded = do_grade(settings, year=year)
            total = Game.objects.count()
            all_done = all(g.graded for g in Game.objects.all())
            self.stdout.write(f'  Graded {graded}/{total} games. All done: {all_done}')

            if not all_done:
                self.stdout.write('  Warning: some games ungraded — advancing anyway for test.')

            # 4. Advance week
            self.stdout.write('  Advancing week...')
            do_advance_week(settings)
            settings.refresh_from_db()
            self.stdout.write(f'  Advanced to week {settings.week}.')

        self.stdout.write(f'\n[test_auto] Done. Site is now at week {settings.week}.')
