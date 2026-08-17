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


class AutoLockRespectsDayFilterTests(TestCase):
    """do_scrape_and_publish took the lock time from get_first_game_dt, which
    ignores the scrape day filter. In a Sunday-only league that pinned the lock
    to the Thursday nighter — excluded from the slate — so picks shut 2.7 days
    before the first game anyone could pick."""

    def setUp(self):
        from . import auto
        self.auto = auto
        self.settings = SiteSettings.get()
        self.settings.week = 3
        self.settings.lock_mode = 'offset'
        self.settings.auto_lock_offset_minutes = 20
        self.settings.auto_tz = 'UTC'
        # Sunday only.
        self.settings.scrape_filter_from_day = 6
        self.settings.scrape_filter_to_day = 6
        self.settings.save()

        self.thursday = datetime(2026, 9, 24, 23, 15, tzinfo=timezone.utc)
        self.sunday = datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)

        # (away, home, underdog_ml, favorite_ml, home_team, game_id, game_dt)
        rows = [
            ('GB', 'ATL', 150, -170, True, '2026_03_GB_ATL', self.thursday),
            ('BUF', 'LAC', 140, -160, True, '2026_03_BUF_LAC', self.sunday),
        ]
        for name, repl in (
            ('scrape', lambda **kw: rows),
            ('get_first_game_dt', lambda **kw: self.thursday),
            ('get_week_type', lambda *a, **kw: 'regular'),
        ):
            self.addCleanup(setattr, auto.scrape_module, name,
                            getattr(auto.scrape_module, name))
            setattr(auto.scrape_module, name, repl)

        self.addCleanup(setattr, auto, 'make_bot_picks', auto.make_bot_picks)
        auto.make_bot_picks = lambda **kw: None

    def test_lock_uses_earliest_game_in_the_slate(self):
        self.auto.do_scrape_and_publish(self.settings, year=2026)
        self.settings.refresh_from_db()

        stored = list(Game.objects.filter(week=3))
        self.assertEqual(len(stored), 1, 'the Thursday game should be filtered out')
        self.assertEqual(stored[0].game_dt, self.sunday)

        self.assertEqual(self.settings.auto_lock_dt,
                         self.sunday - timedelta(minutes=20))
        self.assertGreater(self.settings.auto_lock_dt, self.thursday,
                           'lock must not precede a game excluded from the slate')

    def test_falls_back_when_the_week_stored_no_kickoffs(self):
        # Nothing survives the filter: the week's true first kickoff is all there
        # is to go on, so the old behaviour is still the right fallback.
        self.auto.scrape_module.scrape = lambda **kw: []
        self.auto.do_scrape_and_publish(self.settings, year=2026)
        self.settings.refresh_from_db()

        self.assertEqual(Game.objects.filter(week=3).count(), 0)
        self.assertEqual(self.settings.first_game_dt, self.thursday)


class InboundEmailTests(TestCase):
    """Publishing to the home page by email needs every gate to hold. The one
    that matters is authentication: From is trivially forged, so without it
    anyone knowing the commissioner's address could post to the site."""

    def setUp(self):
        from main.models import LeagueEmail
        self.LeagueEmail = LeagueEmail
        from main import inbound_email
        self.ingest = inbound_email.ingest_message

        self.boss = User.objects.create_user('boss', email='boss@example.com')
        self.boss.profile.email_posts_enabled = True
        self.boss.profile.save()
        # Enough other members that "half the league" is a real threshold.
        self.members = []
        for i in range(4):
            u = User.objects.create_user(f'member{i}', email=f'm{i}@example.com')
            self.members.append(u)
        # A bot has no address and must not count toward the league total.
        bot = User.objects.create_user('bot_x')
        bot.profile.is_bot = True
        bot.profile.save()

    def _raw(self, sender='boss@example.com', to=None, auth='dmarc=pass',
             subject='Week 3', body='Get your picks in.', msgid='<a@example.com>'):
        to = to if to is not None else [m.email for m in self.members]
        lines = [
            f'From: The Commissioner <{sender}>',
            f'To: {", ".join(to)}',
            f'Subject: {subject}',
            f'Message-ID: {msgid}',
            'Date: Mon, 22 Sep 2025 10:00:00 +0000',
        ]
        if auth:
            lines.append(f'Authentication-Results: mx.example.com; {auth}')
        lines += ['Content-Type: text/plain; charset="utf-8"', '', body]
        return '\r\n'.join(lines).encode()

    def test_league_wide_email_from_an_enabled_member_is_published(self):
        obj, reason = self.ingest(self._raw())
        self.assertIsNotNone(obj, reason)
        self.assertEqual(obj.author, self.boss)
        self.assertEqual(obj.subject, 'Week 3')
        self.assertEqual(obj.body, 'Get your picks in.')
        self.assertEqual(obj.recipient_count, 4)
        self.assertTrue(obj.published)

    def test_forged_sender_without_passing_auth_is_rejected(self):
        obj, reason = self.ingest(self._raw(auth='dmarc=fail'))
        self.assertIsNone(obj)
        self.assertIn('authentication failed', reason)

    def test_missing_auth_header_is_rejected(self):
        obj, reason = self.ingest(self._raw(auth=None))
        self.assertIsNone(obj)
        self.assertIn('authentication', reason)

    def test_member_without_the_flag_is_rejected(self):
        self.boss.profile.email_posts_enabled = False
        self.boss.profile.save()
        obj, reason = self.ingest(self._raw())
        self.assertIsNone(obj)
        self.assertIn('email posting enabled', reason)

    def test_unknown_sender_is_rejected(self):
        obj, reason = self.ingest(self._raw(sender='stranger@example.com'))
        self.assertIsNone(obj)
        self.assertIn('not a league member', reason)

    def test_private_note_to_the_site_only_is_rejected(self):
        obj, reason = self.ingest(self._raw(to=['league@putnambowl.com']))
        self.assertIsNone(obj)
        self.assertIn('not a league-wide email', reason)

    def test_list_address_alone_satisfies_league_wide(self):
        with self.settings(LEAGUE_LIST_ADDRESS='league@putnambowl.com'):
            obj, reason = self.ingest(self._raw(to=['league@putnambowl.com']))
        self.assertIsNotNone(obj, reason)

    def test_same_message_is_not_ingested_twice(self):
        first, _ = self.ingest(self._raw())
        self.assertIsNotNone(first)
        second, reason = self.ingest(self._raw())
        self.assertIsNone(second)
        self.assertEqual(reason, 'already ingested')
        self.assertEqual(self.LeagueEmail.objects.count(), 1)

    def test_quoted_reply_is_trimmed_off(self):
        body = ('Reminder: picks close tonight.\r\n\r\n'
                'On Mon, 22 Sep 2025 at 09:00, someone wrote:\r\n'
                '> the entire previous thread\r\n')
        obj, reason = self.ingest(self._raw(body=body))
        self.assertIsNotNone(obj, reason)
        self.assertEqual(obj.body, 'Reminder: picks close tonight.')
        self.assertNotIn('previous thread', obj.body)


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


class AiPickParsingTests(TestCase):
    """The model's reply is untrusted input arriving inside the worker."""

    def setUp(self):
        from . import ai_picks
        self.parse = ai_picks._parse
        self.valid = {1, 2, 3}

    def test_plain_json(self):
        self.assertEqual(
            self.parse('{"1": "team1", "2": "team2"}', self.valid),
            {1: 'team1', 2: 'team2'},
        )

    def test_strips_code_fences(self):
        self.assertEqual(
            self.parse('```json\n{"1": "team2"}\n```', self.valid),
            {1: 'team2'},
        )

    def test_extracts_from_surrounding_prose(self):
        self.assertEqual(
            self.parse('Sure! Here are my picks:\n{"3": "team1"}\nGood luck.', self.valid),
            {3: 'team1'},
        )

    def test_drops_unknown_game_ids(self):
        self.assertEqual(self.parse('{"999": "team1"}', self.valid), {})

    def test_drops_invalid_choices(self):
        self.assertEqual(
            self.parse('{"1": "the packers", "2": "team1"}', self.valid),
            {2: 'team1'},
        )

    def test_garbage_returns_empty(self):
        for junk in ('', 'no idea sorry', '{{{', None, '[1,2,3]'):
            self.assertEqual(self.parse(junk, self.valid), {})


class AiBotPickTests(TestCase):
    """putnambot uses Gemini; a failure must degrade to random, never block."""

    def setUp(self):
        self.settings = SiteSettings.get()
        self.settings.week = 2
        self.settings.save()
        self.bot = User.objects.create_user('putnambot')
        self.bot.profile.is_bot = True
        self.bot.profile.bot_strategy = 'gemini'
        self.bot.profile.save()
        self.g1 = make_game(week=2, team1='Chicago Bears', team2='Green Bay Packers')
        self.g2 = make_game(week=2, team1='Miami Dolphins', team2='New York Jets')

    def _patch(self, fn):
        from . import ai_picks
        self.addCleanup(setattr, ai_picks, 'choose_picks', ai_picks.choose_picks)
        ai_picks.choose_picks = fn

    def test_uses_gemini_choices(self):
        self._patch(lambda games: {self.g1.id: 'team2', self.g2.id: 'team1'})
        make_bot_picks()
        picks = {p.game_id: p.choice for p in Pick.objects.filter(user=self.bot)}
        self.assertEqual(picks, {self.g1.id: 'team2', self.g2.id: 'team1'})

    def test_partial_response_is_filled_in(self):
        self._patch(lambda games: {self.g1.id: 'team2'})
        make_bot_picks()
        picks = Pick.objects.filter(user=self.bot)
        self.assertEqual(picks.count(), 2, 'every game must get a pick')
        self.assertEqual(picks.get(game=self.g1).choice, 'team2')

    def test_gemini_failure_falls_back_to_random(self):
        def boom(games):
            raise RuntimeError('gemini exploded')
        self._patch(boom)
        make_bot_picks()
        self.assertEqual(Pick.objects.filter(user=self.bot).count(), 2)

    def test_random_bots_never_call_gemini(self):
        called = []
        self._patch(lambda games: called.append(1) or {})
        self.bot.profile.bot_strategy = 'random'
        self.bot.profile.save()
        make_bot_picks()
        self.assertEqual(called, [], 'random bots must not hit the API')
        self.assertEqual(Pick.objects.filter(user=self.bot).count(), 2)

    def test_no_api_call_when_picks_already_exist(self):
        Pick.objects.create(user=self.bot, game=self.g1, choice='team1')
        Pick.objects.create(user=self.bot, game=self.g2, choice='team1')
        called = []
        self._patch(lambda games: called.append(1) or {})
        make_bot_picks()
        self.assertEqual(called, [], 'should not re-ask for a fully picked week')


class ApiSplitTests(TestCase):
    """scrape_api and grade_api are independent. nfl-data-py is the only source
    with moneylines; ESPN is the only one with live scores. Driving both jobs
    from one setting made the useful combination impossible."""

    def test_defaults_are_independent_fields(self):
        s = SiteSettings.get()
        s.scrape_api = 'nfl_data_py'
        s.grade_api = 'espn'
        s.save()
        s.refresh_from_db()
        self.assertEqual(s.scrape_api, 'nfl_data_py')
        self.assertEqual(s.grade_api, 'espn')

    def test_scrape_uses_scrape_api(self):
        from . import auto
        s = SiteSettings.get()
        s.scrape_api = 'nfl_data_py'
        s.grade_api = 'espn'
        s.week = 3
        s.save()

        seen = {}
        self.addCleanup(setattr, scrape, 'scrape', scrape.scrape)
        self.addCleanup(setattr, auto.scrape_module, 'get_first_game_dt',
                        auto.scrape_module.get_first_game_dt)
        self.addCleanup(setattr, auto.scrape_module, 'get_week_type',
                        auto.scrape_module.get_week_type)
        auto.scrape_module.scrape = lambda **kw: seen.update(kw) or []
        auto.scrape_module.get_first_game_dt = lambda **kw: None
        auto.scrape_module.get_week_type = lambda *a, **kw: 'regular'

        auto.do_scrape_and_publish(s, year=2025)
        self.assertEqual(seen['api_type'], 'nfl_data_py')

    def test_grade_uses_grade_api(self):
        from . import auto
        s = SiteSettings.get()
        s.scrape_api = 'nfl_data_py'
        s.grade_api = 'espn'
        s.week = 3
        s.save()

        seen = {}
        self.addCleanup(setattr, auto.scrape_module, 'grade', auto.scrape_module.grade)
        auto.scrape_module.grade = lambda **kw: seen.update(kw) or []

        do_grade(s, year=2025)
        self.assertEqual(seen['api_type'], 'espn')


class BiggestUpsetTests(TestCase):
    """team2 is the underdog, so an upset is a team2 win — the view had the two
    sides reversed. Computed on /picks/, which owns the week's slate."""

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
        upset = self.client.get('/picks/').context['biggest_upset']

        self.assertIsNotNone(upset)
        self.assertEqual(upset['winner_full'], 'Carolina Panthers')
        self.assertEqual(upset['pts'], 4.5)

    def test_favorite_win_is_not_an_upset(self):
        game = make_game(week=2, winner='team1', graded=True)
        Pick.objects.create(user=self.user, game=game, choice='team1')

        self.client.login(username='player', password='pw')
        self.assertIsNone(self.client.get('/picks/').context['biggest_upset'])


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
