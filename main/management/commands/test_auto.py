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
        from django.db.models import Q
        from main.models import SiteSettings, Game, Pick
        from main.auto import do_lock_picks, do_grade, do_advance_week, _calculate_points
        from main.teams import ABBREV_TO_TEAM
        from main import scrape as scrape_module

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

            # 1. Scrape + publish (skip get_first_game_dt — not needed for test timing)
            self.stdout.write('  Scraping...')
            games_data = scrape_module.scrape(week=week, api_type='espn', year=year)
            added = 0
            for g in games_data:
                team1 = ABBREV_TO_TEAM.get(g[0], g[0])
                team2 = ABBREV_TO_TEAM.get(g[1], g[1])
                game_id = g[5]
                if Game.objects.filter(Q(game_id=game_id) | Q(team1=team1, team2=team2)).exists():
                    continue
                ug_ml, fav_ml = g[2], g[3]
                pts2 = (_calculate_points(ug_ml, abs(fav_ml)) * settings.multiplier
                        if ug_ml and fav_ml else float(settings.multiplier))
                Game.objects.create(team1=team1, team2=team2, points1=float(settings.multiplier),
                                    points2=pts2, home_team=g[4], game_id=game_id, date=g[6])
                added += 1
            settings.publish = True
            settings.edit = False
            settings.save()
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
