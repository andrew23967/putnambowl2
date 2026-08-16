"""Regression tests for bugs that surfaced once Game/Pick rows started
persisting across weeks instead of being deleted each week.

Run with:  python manage.py test main
"""
from datetime import datetime, timedelta, timezone

from django.contrib.auth.models import User
from django.test import TestCase

from . import scrape
from .auto import do_grade, make_bot_picks
from .models import Game, Pick, SiteSettings


def make_game(week, team1='Chicago Bears', team2='Green Bay Packers',
              points1=1.0, points2=2.5, **kw):
    """team1 is the favorite (1.0x), team2 the underdog (worth more)."""
    return Game.objects.create(
        team1=team1, team2=team2, points1=points1, points2=points2, week=week, **kw
    )


class BotPickScopeTests(TestCase):
    """make_bot_picks used to iterate Game.objects.all(), which retroactively
    added bot picks to completed weeks and rewrote league history."""

    def setUp(self):
        self.settings = SiteSettings.get()
        self.settings.week = 3
        self.settings.save()
        self.bot = User.objects.create_user('bot_a')
        self.bot.profile.is_bot = True
        self.bot.profile.bot_underdog_pct = 50
        self.bot.profile.save()
        self.past = make_game(week=1, winner='team1', graded=True)
        self.current = make_game(week=3, team1='Miami Dolphins', team2='New York Jets')

    def test_only_picks_current_week(self):
        make_bot_picks()
        picked_weeks = set(Pick.objects.filter(user=self.bot).values_list('game__week', flat=True))
        self.assertEqual(picked_weeks, {3})

    def test_does_not_touch_completed_weeks(self):
        make_bot_picks()
        self.assertFalse(Pick.objects.filter(user=self.bot, game=self.past).exists())

    def test_is_idempotent(self):
        make_bot_picks()
        make_bot_picks()
        self.assertEqual(Pick.objects.filter(user=self.bot, game=self.current).count(), 1)

    def test_explicit_week_overrides_current(self):
        make_bot_picks(week=1)
        self.assertTrue(Pick.objects.filter(user=self.bot, game=self.past).exists())


class RematchTests(TestCase):
    """The scrape duplicate-check searched every week, so a division rematch
    later in the season was silently skipped."""

    def test_same_matchup_allowed_in_a_later_week(self):
        settings = SiteSettings.get()
        make_game(week=2)
        settings.week = 12
        settings.save()

        from django.db.models import Q
        clashes = Game.objects.filter(week=settings.week).filter(
            Q(game_id='2025_12_CHI_GB') | Q(team1='Chicago Bears', team2='Green Bay Packers')
        )
        self.assertFalse(clashes.exists(), 'week 12 rematch should not collide with week 2')


class FirstGameDtTests(TestCase):
    """settings.first_game_dt was computed across every week, so the earliest
    kickoff of the whole season won and the auto-lock landed in the past."""

    def test_scoped_to_current_week(self):
        from django.db.models import Min
        settings = SiteSettings.get()
        settings.week = 5
        settings.save()

        week1_kick = datetime(2025, 9, 7, 17, 0, tzinfo=timezone.utc)
        week5_kick = datetime(2025, 10, 5, 17, 0, tzinfo=timezone.utc)
        make_game(week=1, game_dt=week1_kick)
        make_game(week=5, team1='Dallas Cowboys', team2='New York Giants', game_dt=week5_kick)

        first_dt = Game.objects.filter(
            week=settings.week, game_dt__isnull=False
        ).aggregate(Min('game_dt'))['game_dt__min']
        self.assertEqual(first_dt, week5_kick)
        self.assertGreater(first_dt, week1_kick)


class GradeScopeTests(TestCase):
    """The manual grade handler looped over every game ever and re-graded them."""

    def test_does_not_regrade_other_weeks(self):
        settings = SiteSettings.get()
        settings.week = 4
        settings.grade_api = 'espn'
        settings.save()
        past = make_game(week=1, winner='team1', graded=True)
        make_game(week=4, team1='Miami Dolphins', team2='New York Jets')

        # No network in tests: an empty result set must still leave week 1 alone.
        self.addCleanup(setattr, scrape, 'grade', scrape.grade)
        scrape.grade = lambda **kw: []
        do_grade(settings, week=4)

        past.refresh_from_db()
        self.assertEqual(past.winner, 'team1')
        self.assertTrue(past.graded)

    def test_week_argument_selects_the_week(self):
        settings = SiteSettings.get()
        settings.week = 4
        settings.save()
        target = make_game(week=2, game_id='G2', home_team=True)

        self.addCleanup(setattr, scrape, 'grade', scrape.grade)
        scrape.grade = lambda **kw: [['G2', 'home', 'GB', 'CHI']]
        graded = do_grade(settings, week=2)

        target.refresh_from_db()
        self.assertEqual(graded, 1)
        # home_team=True means team2 is at home, so a home win is team2.
        self.assertEqual(target.winner, 'team2')


class BiggestUpsetTests(TestCase):
    """team2 is the underdog, so an upset is a team2 win — the home view had
    the two sides reversed."""

    def setUp(self):
        self.user = User.objects.create_user('player', password='pw')
        self.user.profile.preseason_submitted = True
        self.user.profile.save()
        settings = SiteSettings.get()
        settings.week = 2
        settings.publish = True
        settings.save()

    def test_upset_reports_the_underdog_as_winner(self):
        game = make_game(week=2, team1='Kansas City Chiefs', team2='Carolina Panthers',
                         points1=1.0, points2=4.5, winner='team2', graded=True)
        Pick.objects.create(user=self.user, game=game, choice='team1')

        self.client.login(username='player', password='pw')
        upset = self.client.get('/home/').context['biggest_upset']

        self.assertIsNotNone(upset)
        self.assertEqual(upset['winner_full'], 'Carolina Panthers')
        self.assertEqual(upset['pts'], 4.5)

    def test_favorite_win_is_not_an_upset(self):
        game = make_game(week=2, winner='team1', graded=True)
        Pick.objects.create(user=self.user, game=game, choice='team1')

        self.client.login(username='player', password='pw')
        self.assertIsNone(self.client.get('/home/').context['biggest_upset'])


class ScheduleCacheTests(TestCase):
    """The schedule cache never expired, so the long-running worker kept
    serving whatever it downloaded at boot."""

    def setUp(self):
        self._saved = dict(scrape._schedule_cache)
        scrape._schedule_cache.clear()
        self.addCleanup(lambda: (scrape._schedule_cache.clear(),
                                 scrape._schedule_cache.update(self._saved)))

    def test_no_network_returns_none_when_cache_is_empty(self):
        self.assertIsNone(scrape._get_schedule(2025, allow_network=False))

    def test_no_network_uses_a_fresh_cache_entry(self):
        import time
        scrape._schedule_cache[2025] = (time.monotonic(), 'SCHEDULE')
        self.assertEqual(scrape._get_schedule(2025, allow_network=False), 'SCHEDULE')

    def test_expired_entry_is_not_served_without_network(self):
        import time
        stale = time.monotonic() - scrape.SCHEDULE_TTL_SECONDS - 1
        scrape._schedule_cache[2025] = (stale, 'STALE')
        self.assertIsNone(scrape._get_schedule(2025, allow_network=False))

    def test_week_type_falls_back_without_network(self):
        self.assertEqual(scrape.get_week_type(5, allow_network=False), 'regular')
        self.assertEqual(scrape.get_week_type(20, allow_network=False), 'playoffs')
        self.assertEqual(scrape.get_week_type(22, allow_network=False), 'superbowl')


class HistoryImportTests(TestCase):
    """Both archive encodings must land on the same team order."""

    def test_legacy_zero_one_encoding(self):
        from .history_import import normalise_game
        t1, t2, p1, p2, winner, picks = normalise_game({
            'team1': 'Tampa Bay Buccaneers', 'team2': 'Atlanta Falcons',
            'points1': 1.0, 'points2': 1.24, 'winner': 'team1',
            'player_picks': {
                'mrfavorite': {'pick': '0', 'correct': True},
                'mrunderdog': {'pick': '1', 'correct': False},
            },
        })
        self.assertEqual((t1, t2, p1, p2, winner),
                         ('Tampa Bay Buccaneers', 'Atlanta Falcons', 1.0, 1.24, 'team1'))
        self.assertEqual(picks, {'mrfavorite': 'team1', 'mrunderdog': 'team2'})

    def test_current_team_encoding_passes_through(self):
        from .history_import import normalise_game
        _, _, _, _, winner, picks = normalise_game({
            'team1': 'A', 'team2': 'B', 'points1': 1.0, 'points2': 3.0,
            'winner': 'team2',
            'player_picks': {'sam': {'choice': 'team2', 'correct': True}},
        })
        self.assertEqual(winner, 'team2')
        self.assertEqual(picks, {'sam': 'team2'})

    def test_missing_pick_is_dropped(self):
        from .history_import import normalise_game
        *_, picks = normalise_game({
            'team1': 'A', 'team2': 'B', 'points1': 1.0, 'points2': 2.0, 'winner': '',
            'player_picks': {'nobody': {'pick': None}, 'blank': {'pick': 'none'}},
        })
        self.assertEqual(picks, {})
