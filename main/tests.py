"""Regression tests for bugs that surfaced once Game/Pick rows started
persisting across weeks instead of being deleted each week.

Run with:  python manage.py test main
"""
from datetime import datetime, timedelta, timezone

from django.contrib.auth.models import User
from django.test import TestCase

from . import scrape
from .auto import do_grade, do_scrape_and_publish, make_bot_picks
from .models import (Game, LeagueEmail, Pick, LeagueSettings,
                     WeeklyLeaderboard)
from leagues.models import League


def default_league():
    """The league every test runs in unless it says otherwise (seeded by a migration)."""
    league, _ = League.objects.get_or_create(slug='putnambowl', defaults={'name': 'PutnamBowl'})
    return league


def make_league(slug, name=None):
    return League.objects.create(slug=slug, name=name or slug.title())


def make_member(*args, league=None, role='member', **kwargs):
    """A user in a league. Every account must be in one, or every page 403s."""
    user = User.objects.create_user(*args, **kwargs)
    user.profile.league = league or default_league()
    user.profile.role = role
    user.profile.save()
    return user


def make_game(week, team1='Chicago Bears', team2='Green Bay Packers',
              points1=1.0, points2=2.5, league=None, **kw):
    """team1 is the favorite (1.0x), team2 the underdog (worth more)."""
    return Game.objects.create(
        league=league or default_league(),
        team1=team1, team2=team2, points1=points1, points2=points2, week=week, **kw
    )


class BotPickScopeTests(TestCase):
    """make_bot_picks used to iterate Game.objects.all(), which retroactively
    added bot picks to completed weeks and rewrote league history."""

    def setUp(self):
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 3
        self.settings.save()
        self.bot = make_member('bot_a')
        self.bot.profile.is_bot = True
        self.bot.profile.bot_underdog_pct = 50
        self.bot.profile.save()
        self.past = make_game(week=1, winner='team1', graded=True)
        self.current = make_game(week=3, team1='Miami Dolphins', team2='New York Jets')

    def test_only_picks_current_week(self):
        make_bot_picks(default_league())
        picked_weeks = set(Pick.objects.filter(user=self.bot).values_list('game__week', flat=True))
        self.assertEqual(picked_weeks, {3})

    def test_does_not_touch_completed_weeks(self):
        make_bot_picks(default_league())
        self.assertFalse(Pick.objects.filter(user=self.bot, game=self.past).exists())

    def test_is_idempotent(self):
        make_bot_picks(default_league())
        make_bot_picks(default_league())
        self.assertEqual(Pick.objects.filter(user=self.bot, game=self.current).count(), 1)

    def test_explicit_week_overrides_current(self):
        make_bot_picks(default_league(), week=1)
        self.assertTrue(Pick.objects.filter(user=self.bot, game=self.past).exists())


class RematchTests(TestCase):
    """The scrape duplicate-check searched every week, so a division rematch
    later in the season was silently skipped."""

    def test_same_matchup_allowed_in_a_later_week(self):
        settings = LeagueSettings.for_league(default_league())
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
        settings = LeagueSettings.for_league(default_league())
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
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 3
        self.settings.lock_mode = 'offset'
        self.settings.auto_lock_offset_minutes = 20
        self.settings.auto_tz = 'UTC'
        # Sunday only.
        self.settings.scrape_days = '6'
        self.settings.save()

        self.thursday = datetime(2026, 9, 24, 23, 15, tzinfo=timezone.utc)
        self.sunday = datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)

        # scrape() row shape: (team1, team2, ml1, ml2, team1_is_home, game_id, game_dt)
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
        auto.make_bot_picks = lambda *a, **kw: None

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

    def test_an_empty_slate_is_held_back_not_published(self):
        # A week with no games is always wrong, and publishing it mails the league
        # about an empty slate. It is now held for the retry window instead.
        self.auto.scrape_module.scrape = lambda **kw: []
        self.auto.do_scrape_and_publish(self.settings, year=2026)
        self.settings.refresh_from_db()

        self.assertEqual(Game.objects.filter(week=3).count(), 0)
        self.assertFalse(self.settings.publish, 'an empty week must not publish')
        self.assertIn('No games', self.settings.auto_last_issue)
        self.assertIsNotNone(self.settings.auto_first_attempt_dt)

    def test_falls_back_when_the_week_stored_no_kickoffs(self):
        # When it does go out anyway, the week's true first kickoff is all there
        # is to go on, so the old fallback is still right.
        self.auto.scrape_module.scrape = lambda **kw: []
        self.auto.do_scrape_and_publish(self.settings, year=2026, force=True)
        self.settings.refresh_from_db()

        self.assertEqual(Game.objects.filter(week=3).count(), 0)
        self.assertTrue(self.settings.publish)
        self.assertEqual(self.settings.first_game_dt, self.thursday)


class InboundEmailTests(TestCase):
    """One mailbox does both jobs, and no mailing list is involved. The account's
    publish flag decides whether mail is an announcement or a pick submission, and
    the tagged +picks address overrides it. The gate that actually matters is
    authentication: From is trivially forged, so without it anyone knowing the
    commissioner's address could post to the site."""

    MAILBOX = 'putnambowl.league@gmail.com'
    PICKS = 'putnambowl.league+picks@gmail.com'

    def setUp(self):
        from main.models import LeagueEmail
        self.LeagueEmail = LeagueEmail
        from main import inbound_email, pick_email
        self.ingest = inbound_email.ingest_message

        # Keep the relay and any confirmation out of these tests.
        from main import email_utils
        self.addCleanup(setattr, email_utils, 'relay_to_league',
                        email_utils.relay_to_league)
        email_utils.relay_to_league = lambda *a, **kw: 0
        self.addCleanup(setattr, pick_email, 'send_reply', pick_email.send_reply)
        pick_email.send_reply = lambda *a, **kw: True
        self.addCleanup(setattr, pick_email, '_ask_model', pick_email._ask_model)
        pick_email._ask_model = lambda text, games: '{}'

        self.boss = make_member('boss', email='boss@example.com')
        self.boss.profile.email_posts_enabled = True
        self.boss.profile.save()
        self.members = []
        for i in range(4):
            u = make_member(f'member{i}', email=f'm{i}@example.com')
            self.members.append(u)
        bot = make_member('bot_x')
        bot.profile.is_bot = True
        bot.profile.save()

    def _raw(self, sender='boss@example.com', to=None, auth='dmarc=pass',
             subject='Week 3', body='Get your picks in.', msgid='<a@example.com>'):
        to = to if to is not None else [self.MAILBOX]
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

    def _ingest(self, *a, **kw):
        with self.settings(SMTP_USER=self.MAILBOX, IMAP_USER=self.MAILBOX,
                           PICKS_ADDRESS_TAG='picks'):
            return self.ingest(self._raw(*a, **kw))

    def test_email_from_a_publishing_member_is_published(self):
        obj, reason = self._ingest()
        self.assertIsNotNone(obj, reason)
        self.assertEqual(obj.author, self.boss)
        self.assertEqual(obj.subject, 'Week 3')
        self.assertEqual(obj.body, 'Get your picks in.')
        self.assertTrue(obj.published)

    def test_a_publishing_member_replying_to_a_ballot_is_not_published(self):
        """The footgun this design exists to avoid. The commissioner's plain mail
        is published, so a ballot reply would have broadcast their picks to the
        league. Ballots set Reply-To to the tagged address, which routes it to the
        pick parser instead."""
        obj, reason = self._ingest(to=[self.PICKS], body='KC and the Eagles')
        self.assertIsNone(obj)
        self.assertEqual(self.LeagueEmail.objects.filter(published=True).count(), 0)
        self.assertIn('+picks', reason)

    def test_forged_sender_without_passing_auth_is_rejected(self):
        obj, reason = self._ingest(auth='dmarc=fail')
        self.assertIsNone(obj)
        self.assertIn('authentication failed', reason)

    def test_missing_auth_header_is_rejected(self):
        obj, reason = self._ingest(auth=None)
        self.assertIsNone(obj)
        self.assertIn('authentication', reason)

    def test_member_without_the_flag_submits_picks_rather_than_publishing(self):
        """Turning the flag off does not reject their mail — it means their mail is
        picks, which is what most of the league sends."""
        self.boss.profile.email_posts_enabled = False
        self.boss.profile.save()
        obj, reason = self._ingest()
        self.assertIsNone(obj)
        self.assertEqual(self.LeagueEmail.objects.filter(published=True).count(), 0)
        self.assertIn('not set to publish', reason)

    def test_unknown_sender_is_rejected(self):
        obj, reason = self._ingest(sender='stranger@example.com')
        self.assertIsNone(obj)
        self.assertIn('not a league member', reason)

    def test_same_message_is_not_ingested_twice(self):
        first, _ = self._ingest()
        self.assertIsNotNone(first)
        second, reason = self._ingest()
        self.assertIsNone(second)
        self.assertEqual(reason, 'already ingested')
        self.assertEqual(self.LeagueEmail.objects.count(), 1)

    def test_quoted_reply_is_trimmed_off(self):
        body = ('Reminder: picks close tonight.\r\n\r\n'
                'On Mon, 22 Sep 2025 at 09:00, someone wrote:\r\n'
                '> the entire previous thread\r\n')
        obj, reason = self._ingest(body=body)
        self.assertIsNotNone(obj, reason)
        self.assertEqual(obj.body, 'Reminder: picks close tonight.')
        self.assertNotIn('previous thread', obj.body)


class RecapEmailTests(TestCase):
    """Recaps are recorded to the feed. They reach the league inside the next
    picks-are-live mail, never as a standalone send."""

    def setUp(self):
        from main import email_utils
        from main.models import LeagueEmail
        self.email_utils = email_utils
        self.LeagueEmail = LeagueEmail
        make_member('putnambot')

    def test_outbound_is_suppressed_while_testing(self):
        """The suite talks to Resend and smtplib directly, so Django's locmem
        backend does not protect it - it really did mail fixture addresses once
        SMTP was configured."""
        self.assertTrue(self.email_utils.outbound_suppressed())
        self.assertFalse(self.email_utils.smtp_ready())
        ok, why = self.email_utils.send_via_mailbox('nobody@example.com', 's', 'b')
        self.assertFalse(ok)
        self.assertIn('suppressed', why)

    def test_recap_is_recorded(self):
        obj, created = self.email_utils.record_recap_email(default_league(), 3, 'Week 3 belonged to the underdogs.')
        self.assertTrue(created)
        row = self.LeagueEmail.objects.get(subject='Week 3 recap')
        self.assertIn('PutnamBowl', row.body)
        self.assertEqual(row.source, self.LeagueEmail.SOURCE_SITE)

    def test_league_mail_is_not_signed_by_a_bot(self):
        """Recaps used to append an "I'm PutnamBot, the AI commissioner" signoff
        and be credited to the `putnambot` account. PutnamBot is a player; the
        mailbox is the commissioner."""
        self.email_utils.record_recap_email(default_league(), 3, 'Week 3 belonged to the underdogs.')
        row = self.LeagueEmail.objects.get(subject='Week 3 recap')
        self.assertNotIn('PutnamBot', row.body)
        self.assertIsNone(row.author, "a recap is the league's own mail")

    def test_a_weekly_recap_keeps_one_row_per_week(self):
        """Regenerating replaces week 3's entry rather than adding a version."""
        self.email_utils.record_recap_email(default_league(), 3, 'first write-up', year=2026)
        self.email_utils.record_recap_email(default_league(), 3, 'a better write-up', year=2026)
        rows = self.LeagueEmail.objects.filter(subject='Week 3 recap')
        self.assertEqual(rows.count(), 1)
        self.assertIn('a better write-up', rows.first().body)

    def test_an_unkeyed_record_is_its_own_row(self):
        self.email_utils.record_recap_email(default_league(), None, 'Welcome to the season.', subject='Season preview')
        self.email_utils.record_recap_email(default_league(), None, 'Welcome again.', subject='Season preview')
        self.assertEqual(
            self.LeagueEmail.objects.filter(subject='Season preview').count(), 2)

    def test_next_season_week_one_is_a_new_row(self):
        self.email_utils.record_recap_email(default_league(), 1, 'Week 1 of 2026.', year=2026)
        self.email_utils.record_recap_email(default_league(), 1, 'Week 1 of 2027.', year=2027)
        self.assertEqual(
            self.LeagueEmail.objects.filter(subject='Week 1 recap').count(), 2)

    def test_empty_recap_does_nothing(self):
        self.assertEqual(self.email_utils.record_recap_email(default_league(), 3, '   '), (None, False))
        self.assertEqual(self.LeagueEmail.objects.count(), 0)


class PickEmailHandleTests(TestCase):
    """The submission path: what gets saved and what the sender is told.

    Gemini reads the message, so the model is stubbed here — these cover the
    plumbing around it, which is what can be tested deterministically: validating
    its answer against the real slate, saving, and the confirmation.
    """

    def setUp(self):
        from main import pick_email
        self.pick_email = pick_email
        self.sent = []
        self.addCleanup(setattr, pick_email, 'send_reply', pick_email.send_reply)
        pick_email.send_reply = lambda to, subject, body, in_reply_to=None, **kw: (
            self.sent.append((to, subject, body, in_reply_to)) or True)

        self.addCleanup(setattr, pick_email, '_ask_model', pick_email._ask_model)
        self.model_reply = None
        pick_email._ask_model = lambda text, games: self.model_reply

        self.user = make_member('gramps', email='gramps@example.com')
        self.user.profile.real_name = 'Bill'
        self.user.profile.save()

        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 5
        self.settings.publish = True
        self.settings.lock_picks = False
        self.settings.save()

        self.kc = make_game(week=5, team1='Kansas City Chiefs',
                            team2='Los Angeles Chargers', points1=1.0, points2=2.4)
        self.phi = make_game(week=5, team1='Philadelphia Eagles',
                             team2='Dallas Cowboys', points1=1.0, points2=3.1)

    def test_saves_picks_and_reports_them_back(self):
        self.model_reply = f'{{"{self.kc.id}": "team1", "{self.phi.id}": "team2"}}'
        outcome, _ = self.pick_email.handle(self.user, 'KC and the Cowboys please')

        self.assertEqual(Pick.objects.filter(user=self.user).count(), 2)
        self.assertEqual(Pick.objects.get(user=self.user, game=self.kc).choice, 'team1')
        self.assertEqual(Pick.objects.get(user=self.user, game=self.phi).choice, 'team2')

        to, subject, body, _ = self.sent[0]
        self.assertEqual(to, 'gramps@example.com')
        self.assertIn('Week 5', subject)
        self.assertIn('Bill', body)
        # The reply must state the picks, not just a count.
        self.assertIn('Kansas City Chiefs over Los Angeles Chargers', body)
        self.assertIn('Dallas Cowboys over Philadelphia Eagles', body)
        self.assertIn('2 of 2 games', body)
        self.assertIn('saved 2/2', outcome)

    def test_partial_answer_saves_what_it_got_and_asks_about_the_rest(self):
        self.model_reply = f'{{"{self.kc.id}": "team1"}}'
        self.pick_email.handle(self.user, 'just KC for now')

        self.assertEqual(Pick.objects.filter(user=self.user).count(), 1)
        body = self.sent[0][2]
        self.assertIn('could not tell who you wanted', body)
        self.assertIn('Philadelphia Eagles  /  Dallas Cowboys', body)
        self.assertIn('1 of 2 games', body)

    def test_a_hallucinated_game_id_is_discarded(self):
        """Reading the message is delegated; trusting the answer is not."""
        self.model_reply = f'{{"999": "team1", "{self.kc.id}": "team2"}}'
        self.pick_email.handle(self.user, 'the Chargers')

        self.assertEqual(Pick.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Pick.objects.get(user=self.user).game, self.kc)

    def test_a_nonsense_choice_is_discarded(self):
        self.model_reply = f'{{"{self.kc.id}": "the Chiefs"}}'
        self.pick_email.handle(self.user, 'chiefs')
        self.assertEqual(Pick.objects.filter(user=self.user).count(), 0)

    def test_prose_around_the_json_is_tolerated(self):
        self.model_reply = (f'Sure! Here are the picks:\n```json\n'
                            f'{{"{self.kc.id}": "team1"}}\n```')
        self.pick_email.handle(self.user, 'KC')
        self.assertEqual(Pick.objects.get(user=self.user, game=self.kc).choice, 'team1')

    def test_resending_updates_rather_than_duplicates(self):
        self.model_reply = f'{{"{self.kc.id}": "team1"}}'
        self.pick_email.handle(self.user, 'KC')
        self.model_reply = f'{{"{self.kc.id}": "team2"}}'
        self.pick_email.handle(self.user, 'Chargers actually')

        self.assertEqual(Pick.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Pick.objects.get(user=self.user, game=self.kc).choice, 'team2')

    def test_locked_week_saves_nothing_and_says_so(self):
        self.settings.lock_picks = True
        self.settings.save()
        outcome, _ = self.pick_email.handle(self.user, 'KC and the Cowboys')
        self.assertEqual(Pick.objects.filter(user=self.user).count(), 0)
        self.assertIn('locked', self.sent[0][2])
        self.assertIn('locked', outcome)

    def test_unpublished_week_saves_nothing_and_says_so(self):
        self.settings.publish = False
        self.settings.save()
        self.pick_email.handle(self.user, 'KC')
        self.assertEqual(Pick.objects.filter(user=self.user).count(), 0)
        self.assertIn("isn't open for picks yet", self.sent[0][2])

    def test_unreadable_message_saves_nothing_and_says_so(self):
        self.model_reply = '{}'
        self.pick_email.handle(self.user, 'hi hope you are well, talk soon')
        self.assertEqual(Pick.objects.filter(user=self.user).count(), 0)
        self.assertIn('could not work out any picks', self.sent[0][2])

    def test_model_unavailable_is_reported_not_silently_swallowed(self):
        """A missing key must not look like "you made no picks" — the sender is
        waiting on a confirmation that would otherwise never arrive."""
        self.model_reply = None
        outcome, _ = self.pick_email.handle(self.user, 'KC and the Cowboys')

        self.assertEqual(Pick.objects.filter(user=self.user).count(), 0)
        self.assertIn('unavailable', self.sent[0][2])
        self.assertIn('model unavailable', outcome)


class EmailSwitchTests(TestCase):
    """The Emails page switches must actually stop mail, and a prompt edit must
    never be able to remove the data the prompt depends on."""

    def setUp(self):
        from main import auto, email_utils
        self.auto = auto
        self.email_utils = email_utils
        self.settings_obj = LeagueSettings.for_league(default_league())
        self.settings_obj.week = 4
        self.settings_obj.save()
        make_member('putnambot')

    def test_relay_switch_off_forwards_to_nobody(self):
        from main.models import LeagueEmail
        self.settings_obj.email_relay = False
        self.settings_obj.save()
        row = LeagueEmail.objects.create(league=default_league(), 
            subject='Notice', body='hello', sent_at=datetime(2026, 1, 1,
                                                             tzinfo=timezone.utc),
            message_id='<x@example.com>')
        self.assertEqual(
            self.email_utils.relay_to_league(row, sender_email='boss@example.com'), 0)

    def test_confirmation_switch_off_sends_no_reply(self):
        from main import pick_email
        self.settings_obj.email_confirmations = False
        self.settings_obj.save()
        self.assertFalse(pick_email.send_reply('a@example.com', 's', 'b', settings=self.settings_obj))

    def test_editing_the_prompt_cannot_remove_the_data(self):
        """The instructions are the commissioner's; the standings and results are
        not optional. Whatever they write, the facts are appended."""
        make_game(week=3, winner='team1', graded=True)
        user = make_member('player')
        Pick.objects.create(user=user, game=Game.objects.get(week=3),
                            choice='team1')

        prompt = self.auto.build_recap_prompt(default_league(), 3, instructions='Be brief.')
        self.assertTrue(prompt.startswith('Be brief.'))
        # Both halves of the data survive: the computed angles and the table.
        self.assertIn('things worth writing about', prompt)
        self.assertIn('BEST WEEK', prompt)
        self.assertIn('Points scored this week', prompt)
        self.assertIn('player', prompt)
        self.assertIn(self.auto.RECAP_FORMAT_RULES, prompt)

    def test_week_placeholder_is_substituted(self):
        make_game(week=3, winner='team1', graded=True)
        user = make_member('player')
        Pick.objects.create(user=user, game=Game.objects.get(week=3), choice='team1')

        prompt = self.auto.build_recap_prompt(default_league(), 3, instructions='Recap week {week}.')
        self.assertIn('Recap week 3.', prompt)

    def test_a_stray_brace_in_the_prompt_does_not_raise(self):
        """User-edited text, so replace() not format() — a stray brace must not
        blow up recap generation."""
        make_game(week=3, winner='team1', graded=True)
        user = make_member('player')
        Pick.objects.create(user=user, game=Game.objects.get(week=3), choice='team1')

        prompt = self.auto.build_recap_prompt(default_league(), 3, instructions='Use {curly} braces {')
        self.assertIn('{curly}', prompt)

    def test_blank_prompt_falls_back_to_the_default(self):
        make_game(week=3, winner='team1', graded=True)
        user = make_member('player')
        Pick.objects.create(user=user, game=Game.objects.get(week=3), choice='team1')

        self.settings_obj.recap_prompt = ''
        self.settings_obj.save()
        prompt = self.auto.build_recap_prompt(default_league(), 3)
        self.assertIn('factual weekly recap', prompt)


class PublishTogglePicksLiveTests(TestCase):
    """Flipping Publish on is how a week normally goes live, and it used to send
    nothing: the Scrape button does not publish, and only mails when the week was
    already published. The league heard nothing, ballot included."""

    def setUp(self):
        from main import email_utils
        self.email_utils = email_utils
        self.sent = []
        self.addCleanup(setattr, email_utils, 'send_picks_published_email',
                        email_utils.send_picks_published_email)
        # Patched where views looks it up: it imports inside the branch.
        email_utils.send_picks_published_email = lambda s: self.sent.append(s.week)

        self.boss = make_member('boss', password='pw', is_staff=True,
                                             is_superuser=True)
        self.settings_obj = LeagueSettings.for_league(default_league())
        self.settings_obj.week = 4
        self.settings_obj.publish = False
        self.settings_obj.save()
        make_game(week=4)
        self.client.login(username='boss', password='pw')

    def _toggle(self):
        return self.client.post('/dashboard/picks/', {'toggle_publish': '1'})

    def test_publishing_mails_the_league(self):
        self._toggle()
        self.settings_obj.refresh_from_db()
        self.assertTrue(self.settings_obj.publish)
        self.assertEqual(self.sent, [4])

    def test_unpublishing_mails_nobody(self):
        self.settings_obj.publish = True
        self.settings_obj.save()
        self._toggle()
        self.settings_obj.refresh_from_db()
        self.assertFalse(self.settings_obj.publish)
        self.assertEqual(self.sent, [])

    def test_only_the_transition_sends(self):
        self._toggle()          # off -> on, sends
        self._toggle()          # on -> off, silent
        self._toggle()          # off -> on, sends again
        self.assertEqual(self.sent, [4, 4])


class RelayTests(TestCase):
    """The site holds the real membership; the Google Group does not, since most of
    the league is not in it. So one message to the group must reach everyone."""

    def setUp(self):
        from main import email_utils, inbound_email
        self.email_utils = email_utils
        self.ingest = inbound_email.ingest_message

        self.forwarded = []
        self.addCleanup(setattr, email_utils, 'send_via_mailbox',
                        email_utils.send_via_mailbox)
        email_utils.send_via_mailbox = lambda to, subject, body, **kw: (
            self.forwarded.append((to, subject, body, kw.get('reply_to')))
            or (True, 'sent'))

        # Run the relay inline so the test does not depend on thread timing.
        import threading
        real_thread = threading.Thread
        self.addCleanup(setattr, threading, 'Thread', real_thread)

        def inline(target=None, **kwargs):
            class _T:
                def start(inner):
                    target()
            return _T()
        threading.Thread = inline

        self.boss = make_member('boss', email='boss@example.com')
        self.boss.profile.email_posts_enabled = True
        self.boss.profile.real_name = 'The Commissioner'
        self.boss.profile.save()
        for i in range(4):
            make_member(f'member{i}', email=f'm{i}@example.com')
        bot = make_member('bot_x')
        bot.profile.is_bot = True
        bot.profile.save()

    def _raw(self, to, msgid='<r1@example.com>'):
        return '\r\n'.join([
            'From: The Commissioner <boss@example.com>',
            f'To: {to}',
            'Subject: Week 1 is live',
            f'Message-ID: {msgid}',
            'Date: Mon, 22 Sep 2025 10:00:00 +0000',
            'Authentication-Results: mx.example.com; dmarc=pass',
            'Content-Type: text/plain; charset="utf-8"',
            '', 'Picks are open, get them in.',
        ]).encode()

    def test_group_email_is_forwarded_to_every_member(self):
        with self.settings(SMTP_USER='mailbox@gmail.com'):
            obj, reason = self.ingest(self._raw('league@putnambowl.com'))

        self.assertIsNotNone(obj, reason)
        got = sorted(to for to, *_ in self.forwarded)
        self.assertEqual(got, ['m0@example.com', 'm1@example.com',
                               'm2@example.com', 'm3@example.com'])
        self.assertIn('relayed to 4', reason)

    def test_the_sender_and_the_mailbox_are_not_forwarded_to(self):
        with self.settings(SMTP_USER='boss@example.com'):
            self.ingest(self._raw('league@putnambowl.com'))
        self.assertNotIn('boss@example.com', [to for to, *_ in self.forwarded])

    def test_already_copied_members_are_not_forwarded_to(self):
        """Nobody gets it twice when some are copied directly."""
        with self.settings():
            self.ingest(self._raw(
                'league@putnambowl.com, m0@example.com, m1@example.com'))
        got = sorted(to for to, *_ in self.forwarded)
        self.assertEqual(got, ['m2@example.com', 'm3@example.com'])

    def test_replies_go_to_the_commissioner_not_the_mailbox(self):
        """A reply landing in our mailbox would be parsed as a pick submission."""
        with self.settings():
            self.ingest(self._raw('league@putnambowl.com'))
        for _, _, _, reply_to in self.forwarded:
            self.assertEqual(reply_to, 'boss@example.com')

    def test_the_forward_says_who_sent_it(self):
        with self.settings():
            self.ingest(self._raw('league@putnambowl.com'))
        body = self.forwarded[0][2]
        self.assertIn('Picks are open, get them in.', body)
        self.assertIn('The Commissioner', body)

    def test_deleting_a_feed_row_does_not_re_relay_it(self):
        """The poller scans a rolling window, so a deleted message is still in the
        mailbox. If dedupe read the feed, deleting a row would forward that email
        to the entire league a second time."""
        from main.models import LeagueEmail
        with self.settings(SMTP_USER='mailbox@gmail.com'):
            obj, _ = self.ingest(self._raw('mailbox@gmail.com'))
            self.assertIsNotNone(obj)
            self.assertEqual(len(self.forwarded), 4)

            LeagueEmail.objects.all().delete()
            self.forwarded.clear()

            again, reason = self.ingest(self._raw('mailbox@gmail.com'))
        self.assertIsNone(again)
        self.assertEqual(reason, 'already ingested')
        self.assertEqual(self.forwarded, [], 'must not forward a second time')

    def test_a_rejected_message_is_retried_after_the_flag_is_turned_on(self):
        """Config rejections are deliberately not marked processed, so enabling
        someone's publishing picks their message up on the next poll."""
        self.boss.profile.email_posts_enabled = False
        self.boss.profile.save()
        from main import pick_email
        self.addCleanup(setattr, pick_email, '_ask_model', pick_email._ask_model)
        pick_email._ask_model = lambda text, games: '{}'
        self.addCleanup(setattr, pick_email, 'send_reply', pick_email.send_reply)
        pick_email.send_reply = lambda *a, **kw: True

        with self.settings(SMTP_USER='mailbox@gmail.com'):
            # Read as picks while the flag is off, which *is* an action, so it is
            # recorded — the retry case that matters is an unknown sender.
            self.ingest(self._raw('mailbox@gmail.com', msgid='<x1@example.com>'))

            stranger = self._raw('mailbox@gmail.com', msgid='<x2@example.com>').replace(
                b'boss@example.com', b'newcomer@example.com')
            obj, reason = self.ingest(stranger)
            self.assertIsNone(obj)
            self.assertIn('not a league member', reason)

            # They get an account, and the next poll picks the message up.
            newcomer = make_member('newcomer',
                                                email='newcomer@example.com')
            newcomer.profile.email_posts_enabled = True
            newcomer.profile.save()
            obj, reason = self.ingest(stranger)
        self.assertIsNotNone(obj, reason)

    def test_a_model_outage_defers_instead_of_dropping_the_picks(self):
        """Gemini answering 503 is temporary and must not cost someone their
        picks. The submission is left unprocessed so the next poll retries, and
        the sender is not told about an outage that may clear on its own."""
        from main import inbound_email, pick_email
        from main.models import ProcessedEmail

        told = []
        self.addCleanup(setattr, pick_email, 'send_reply', pick_email.send_reply)
        pick_email.send_reply = lambda *a, **kw: told.append(a[2]) or True
        self.addCleanup(setattr, pick_email, '_ask_model', pick_email._ask_model)
        pick_email._ask_model = lambda text, games: None      # unreachable

        s = LeagueSettings.for_league(default_league())
        s.week = 5
        s.publish = True
        s.lock_picks = False
        s.save()
        make_game(week=5)

        raw = self._raw('mailbox+picks@gmail.com')
        with self.settings(SMTP_USER='mailbox@gmail.com', PICKS_ADDRESS_TAG='picks'):
            obj, reason = self.ingest(raw)
            self.assertIsNone(obj)
            self.assertIn('will retry', reason)
            self.assertTrue(ProcessedEmail.objects.get(
                message_id='<r1@example.com>').deferred)
            self.assertEqual(told, [], 'must not report an outage that may clear')

            # The model comes back, and the next poll picks it up.
            pick_email._ask_model = lambda text, games: (
                f'{{"{Game.objects.get(week=5).id}": "team1"}}')
            obj, reason = self.ingest(raw)

        self.assertIn('saved 1/1', reason)
        self.assertFalse(ProcessedEmail.objects.get(
            message_id='<r1@example.com>').deferred)
        self.assertEqual(Pick.objects.filter(user=self.boss).count(), 1)

    def test_a_retry_does_not_collide_with_the_row_from_the_first_attempt(self):
        """A retried submission may already have a LeagueEmail row. create() threw
        a UNIQUE violation there — after the picks had been saved and the member
        told, so it looked like a total failure when it was actually a success."""
        from main import pick_email
        from main.models import LeagueEmail

        self.addCleanup(setattr, pick_email, 'send_reply', pick_email.send_reply)
        pick_email.send_reply = lambda *a, **kw: True

        s = LeagueSettings.for_league(default_league())
        s.week = 5
        s.publish = True
        s.lock_picks = False
        s.save()
        game = make_game(week=5)

        # A row already exists for this message, as it would after a first attempt.
        LeagueEmail.objects.create(league=default_league(), 
            message_id='<r1@example.com>', subject='old', body='old',
            sent_at=datetime(2026, 1, 1, tzinfo=timezone.utc), published=False)

        self.addCleanup(setattr, pick_email, '_ask_model', pick_email._ask_model)
        pick_email._ask_model = lambda text, games: f'{{"{game.id}": "team1"}}'

        with self.settings(SMTP_USER='mailbox@gmail.com', PICKS_ADDRESS_TAG='picks'):
            obj, reason = self.ingest(self._raw('mailbox+picks@gmail.com'))

        self.assertIn('saved 1/1', reason)
        self.assertEqual(LeagueEmail.objects.filter(
            message_id='<r1@example.com>').count(), 1)
        self.assertEqual(Pick.objects.filter(user=self.boss).count(), 1)

    def test_a_long_outage_eventually_tells_the_sender(self):
        from datetime import timedelta as _td

        from main import pick_email
        from main.models import ProcessedEmail

        told = []
        self.addCleanup(setattr, pick_email, 'send_reply', pick_email.send_reply)
        pick_email.send_reply = lambda *a, **kw: told.append(a[2]) or True
        self.addCleanup(setattr, pick_email, '_ask_model', pick_email._ask_model)
        pick_email._ask_model = lambda text, games: None

        s = LeagueSettings.for_league(default_league())
        s.week = 5
        s.publish = True
        s.lock_picks = False
        s.save()
        make_game(week=5)

        raw = self._raw('mailbox+picks@gmail.com')
        with self.settings(SMTP_USER='mailbox@gmail.com', PICKS_ADDRESS_TAG='picks'):
            self.ingest(raw)
            # Pretend the first attempt was long enough ago to give up on.
            row = ProcessedEmail.objects.get(message_id='<r1@example.com>')
            row.seen_at = row.seen_at - _td(hours=2)
            row.save()
            obj, reason = self.ingest(raw)

        self.assertIn('gave up', reason)
        self.assertFalse(ProcessedEmail.objects.get(
            message_id='<r1@example.com>').deferred, 'must stop retrying')
        self.assertEqual(len(told), 1)
        self.assertIn('unavailable', told[0])

    def test_a_pick_submission_is_not_relayed(self):
        """Mail to the tagged address is private picks — forwarding it would leak
        someone's picks to the league before lock."""
        from main import pick_email
        self.addCleanup(setattr, pick_email, 'send_reply', pick_email.send_reply)
        pick_email.send_reply = lambda *a, **kw: True
        self.addCleanup(setattr, pick_email, '_ask_model', pick_email._ask_model)
        pick_email._ask_model = lambda text, games: '{}'

        with self.settings(SMTP_USER='mailbox@gmail.com',
                           PICKS_ADDRESS_TAG='picks'):
            self.ingest(self._raw('mailbox+picks@gmail.com'))
        self.assertEqual(self.forwarded, [])


class PickEmailRoutingTests(TestCase):
    """Mail to the list publishes; mail direct to the mailbox submits picks. A
    submission must never reach the feed — picks are private until lock."""

    def setUp(self):
        from main import inbound_email, pick_email
        from main.models import LeagueEmail
        self.ingest = inbound_email.ingest_message
        self.LeagueEmail = LeagueEmail
        self.addCleanup(setattr, pick_email, 'send_reply', pick_email.send_reply)
        self.sent = []
        pick_email.send_reply = lambda to, subject, body, in_reply_to=None, **kw: (
            self.sent.append((to, subject, body, in_reply_to)) or True)

        self.user = make_member('gramps', email='gramps@example.com')
        # Deliberately WITHOUT email_posts_enabled: submitting picks needs no
        # publishing privilege.
        self.user.profile.save()
        for i in range(4):
            make_member(f'member{i}', email=f'm{i}@example.com')

        s = LeagueSettings.for_league(default_league())
        s.week = 5
        s.publish = True
        s.lock_picks = False
        s.save()
        self.game = make_game(week=5, team1='Kansas City Chiefs',
                             team2='Los Angeles Chargers')

        self.addCleanup(setattr, pick_email, '_ask_model', pick_email._ask_model)
        pick_email._ask_model = lambda text, games: (
            f'{{"{self.game.id}": "team1"}}')

    def _raw(self, to, body, msgid='<p1@example.com>'):
        return '\r\n'.join([
            f'From: Bill <gramps@example.com>',
            f'To: {to}',
            'Subject: my picks',
            f'Message-ID: {msgid}',
            'Date: Mon, 22 Sep 2025 10:00:00 +0000',
            'Authentication-Results: mx.example.com; dmarc=pass',
            'Content-Type: text/plain; charset="utf-8"',
            '', body,
        ]).encode()

    def test_direct_email_submits_picks_and_is_not_published(self):
        obj, reason = self.ingest(self._raw('putnambowl.league@gmail.com', 'KC please'))
        self.assertIsNone(obj, 'a pick submission must not become a feed post')
        self.assertIn('saved 1/1', reason)
        self.assertEqual(Pick.objects.get(user=self.user, game=self.game).choice, 'team1')
        self.assertEqual(self.LeagueEmail.objects.filter(published=True).count(), 0)
        # Stored unpublished so the next poll does not parse it again.
        self.assertEqual(self.LeagueEmail.objects.filter(published=False).count(), 1)

        to, subject, body, in_reply_to = self.sent[0]
        self.assertEqual(to, 'gramps@example.com')
        # Threaded onto their own message, and titled as a reply to it, so the
        # confirmation lands in the conversation they started.
        self.assertEqual(in_reply_to, '<p1@example.com>')
        self.assertEqual(subject, 'Re: my picks')

    def test_the_same_submission_is_not_processed_twice(self):
        self.ingest(self._raw('putnambowl.league@gmail.com', 'KC please'))
        obj, reason = self.ingest(self._raw('putnambowl.league@gmail.com', 'KC please'))
        self.assertIsNone(obj)
        self.assertEqual(reason, 'already ingested')
        self.assertEqual(len(self.sent), 1, 'must not reply twice to one email')

    def test_a_member_without_the_flag_has_their_mail_read_as_picks(self):
        """The flag decides. Off — as it is for most of the league — means their
        mail is a pick submission, not something to publish."""
        obj, reason = self.ingest(
            self._raw('putnambowl.league@gmail.com', 'the Chiefs please'))
        self.assertIsNone(obj)
        self.assertIn('not set to publish', reason)
        self.assertEqual(self.LeagueEmail.objects.filter(published=True).count(), 0)
        self.assertEqual(Pick.objects.filter(user=self.user).count(), 1)


class GradeScopeTests(TestCase):
    """The manual grade handler looped over every game ever and re-graded them."""

    def test_does_not_regrade_other_weeks(self):
        settings = LeagueSettings.for_league(default_league())
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
        settings = LeagueSettings.for_league(default_league())
        settings.week = 4
        settings.save()
        target = make_game(week=2, game_id='G2', team1_is_home=False)

        self.addCleanup(setattr, scrape, 'grade', scrape.grade)
        scrape.grade = lambda **kw: [['G2', 'home', 'GB', 'CHI']]
        graded = do_grade(settings, week=2)

        target.refresh_from_db()
        self.assertEqual(graded, 1)
        # team2 (GB) is the home side here, so a home win is team2.
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
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 2
        self.settings.save()
        self.bot = make_member('putnambot')
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
        make_bot_picks(default_league())
        picks = {p.game_id: p.choice for p in Pick.objects.filter(user=self.bot)}
        self.assertEqual(picks, {self.g1.id: 'team2', self.g2.id: 'team1'})

    def test_partial_response_is_filled_in(self):
        self._patch(lambda games: {self.g1.id: 'team2'})
        make_bot_picks(default_league())
        picks = Pick.objects.filter(user=self.bot)
        self.assertEqual(picks.count(), 2, 'every game must get a pick')
        self.assertEqual(picks.get(game=self.g1).choice, 'team2')

    def test_gemini_failure_falls_back_to_random(self):
        def boom(games):
            raise RuntimeError('gemini exploded')
        self._patch(boom)
        make_bot_picks(default_league())
        self.assertEqual(Pick.objects.filter(user=self.bot).count(), 2)

    def test_random_bots_never_call_gemini(self):
        called = []
        self._patch(lambda games: called.append(1) or {})
        self.bot.profile.bot_strategy = 'random'
        self.bot.profile.save()
        make_bot_picks(default_league())
        self.assertEqual(called, [], 'random bots must not hit the API')
        self.assertEqual(Pick.objects.filter(user=self.bot).count(), 2)

    def test_no_api_call_when_picks_already_exist(self):
        Pick.objects.create(user=self.bot, game=self.g1, choice='team1')
        Pick.objects.create(user=self.bot, game=self.g2, choice='team1')
        called = []
        self._patch(lambda games: called.append(1) or {})
        make_bot_picks(default_league())
        self.assertEqual(called, [], 'should not re-ask for a fully picked week')


class ApiSplitTests(TestCase):
    """scrape_api and grade_api are independent. nfl-data-py is the only source
    with moneylines; ESPN is the only one with live scores. Driving both jobs
    from one setting made the useful combination impossible."""

    def test_defaults_are_independent_fields(self):
        s = LeagueSettings.for_league(default_league())
        s.scrape_api = 'nfl_data_py'
        s.grade_api = 'espn'
        s.save()
        s.refresh_from_db()
        self.assertEqual(s.scrape_api, 'nfl_data_py')
        self.assertEqual(s.grade_api, 'espn')

    def test_scrape_uses_scrape_api(self):
        from . import auto
        s = LeagueSettings.for_league(default_league())
        s.scrape_api = 'nfl_data_py'
        s.grade_api = 'espn'
        s.week = 3
        s.save()

        calls = []
        self.addCleanup(setattr, scrape, 'scrape', scrape.scrape)
        self.addCleanup(setattr, auto.scrape_module, 'get_first_game_dt',
                        auto.scrape_module.get_first_game_dt)
        self.addCleanup(setattr, auto.scrape_module, 'get_week_type',
                        auto.scrape_module.get_week_type)
        auto.scrape_module.scrape = lambda **kw: calls.append(kw) or []
        auto.scrape_module.get_first_game_dt = lambda **kw: None
        auto.scrape_module.get_week_type = lambda *a, **kw: 'regular'

        auto.do_scrape_and_publish(s, year=2025, force=True)
        # The first call is the real scrape; the second is the cross-check, which
        # deliberately asks the other source.
        self.assertEqual(calls[0]['api_type'], 'nfl_data_py')
        self.assertEqual([c['api_type'] for c in calls[1:]], ['espn'])

    def test_grade_uses_grade_api(self):
        from . import auto
        s = LeagueSettings.for_league(default_league())
        s.scrape_api = 'nfl_data_py'
        s.grade_api = 'espn'
        s.week = 3
        s.save()

        seen = {}
        self.addCleanup(setattr, auto.scrape_module, 'grade', auto.scrape_module.grade)
        auto.scrape_module.grade = lambda **kw: seen.update(kw) or []

        do_grade(s, year=2025)
        self.assertEqual(seen['api_type'], 'espn')


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


class ConferenceSplitTests(TestCase):
    """NFC_TEAMS and AFC_TEAMS were TEAMS[:16] and TEAMS[16:] — an alphabetical
    cut, so the "NFC" half held the Ravens, Bills, Bengals, Browns, Broncos,
    Texans, Colts, Jaguars and Chiefs. Nothing read them, so nothing failed. The
    preseason form reads them now."""

    def test_each_conference_has_sixteen_teams(self):
        from .teams import NFC_TEAMS, AFC_TEAMS
        self.assertEqual(len(NFC_TEAMS), 16)
        self.assertEqual(len(AFC_TEAMS), 16)

    def test_the_two_halves_partition_the_league(self):
        from .teams import TEAMS, NFC_TEAMS, AFC_TEAMS
        nfc = {n for n, _ in NFC_TEAMS}
        afc = {n for n, _ in AFC_TEAMS}
        self.assertEqual(nfc & afc, set(), 'a team is in both conferences')
        self.assertEqual(nfc | afc, {n for n, _ in TEAMS})

    def test_known_teams_land_in_the_right_conference(self):
        from .teams import NFC_TEAMS, AFC_TEAMS
        nfc = {n for n, _ in NFC_TEAMS}
        afc = {n for n, _ in AFC_TEAMS}
        # The nine the alphabetical split misfiled, plus a couple of controls.
        for team in ('Baltimore Ravens', 'Buffalo Bills', 'Cincinnati Bengals',
                     'Cleveland Browns', 'Denver Broncos', 'Houston Texans',
                     'Indianapolis Colts', 'Jacksonville Jaguars',
                     'Kansas City Chiefs', 'Miami Dolphins'):
            self.assertIn(team, afc, f'{team} should be AFC')
        for team in ('Arizona Cardinals', 'Dallas Cowboys', 'Green Bay Packers',
                     'Philadelphia Eagles', 'San Francisco 49ers',
                     'Seattle Seahawks', 'Washington Commanders'):
            self.assertIn(team, nfc, f'{team} should be NFC')

    def test_champion_fields_only_offer_their_own_conference(self):
        from .forms import PreseasonForm
        from .teams import NFC_TEAMS, AFC_TEAMS
        user = make_member('confuser', password='pw')
        form = PreseasonForm(user)
        self.assertEqual(list(form.fields['nfc_champ'].choices), list(NFC_TEAMS))
        self.assertEqual(list(form.fields['afc_champ'].choices), list(AFC_TEAMS))


class PreseasonEditWindowTests(TestCase):
    """Submitting is not the deadline — week 1's kickoff is. These stay editable
    until that week's picks lock, rather than locking the moment they were first
    saved."""

    def setUp(self):
        self.user = make_member('presuser', password='pw')
        self.client.login(username='presuser', password='pw')
        self.settings = LeagueSettings.for_league(default_league())

    def _post(self):
        return self.client.post('/preseason/', {
            'big_loser': 'Carolina Panthers',
            'nfc_champ': 'Philadelphia Eagles',
            'afc_champ': 'Kansas City Chiefs',
            'superbowl_winner': 'Kansas City Chiefs',
        })

    def test_already_submitted_picks_can_still_be_changed_before_the_lock(self):
        self.settings.week = 1
        self.settings.lock_picks = False
        self.settings.save()
        self.user.profile.preseason_submitted = True
        self.user.profile.nfc_champ = 'Dallas Cowboys'
        self.user.profile.save()

        self._post()
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.nfc_champ, 'Philadelphia Eagles')

    def test_locking_week_one_picks_closes_them(self):
        # The deadline that matters: still week 1, but the slate has shut.
        self.settings.week = 1
        self.settings.lock_picks = True
        self.settings.save()
        self.user.profile.preseason_submitted = True
        self.user.profile.nfc_champ = 'Dallas Cowboys'
        self.user.profile.save()

        self._post()
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.nfc_champ, 'Dallas Cowboys')

    def test_later_weeks_reject_the_edit(self):
        self.settings.week = 2
        self.settings.lock_picks = False
        self.settings.save()
        self.user.profile.preseason_submitted = True
        self.user.profile.nfc_champ = 'Dallas Cowboys'
        self.user.profile.save()

        self._post()
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.nfc_champ, 'Dallas Cowboys')

    def test_superbowl_winner_must_be_one_of_the_two_champions(self):
        from .forms import PreseasonForm
        form = PreseasonForm(self.user, {
            'big_loser': 'Carolina Panthers',
            'nfc_champ': 'Philadelphia Eagles',
            'afc_champ': 'Kansas City Chiefs',
            'superbowl_winner': 'Detroit Lions',
        })
        self.assertFalse(form.is_valid())

class NavPageRenderTests(TestCase):
    """Every page reachable from the nav must actually render.

    `manage.py check` compiles no templates, so a malformed tag or a `{% url %}`
    naming a deleted route is a render-time 500 that passes every other gate. Two
    shipped that way: a stale `main:emails` link took out the Emails dashboard,
    and `field.field.widget.__class__.__name__` — illegal in a Django template,
    variables may not begin with underscores — took out My Profile, which sits in
    the nav for every signed-in user.
    """

    PAGES = ['main:home', 'main:picks', 'main:rules', 'main:analytics',
             'main:pick_history', 'main:preseason', 'main:members',
             'main:pickdash', 'main:emaildash', 'main:accountdash',
             'main:seasons', 'main:rulesdash',
             'accounts:user_profile', 'accounts:password_change', 'leagues:index']

    def setUp(self):
        self.user = make_member('nav_tester', password='pw', email='n@x.com')
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        LeagueSettings.for_league(default_league())

    def _ok(self, name, **kw):
        from django.urls import reverse
        url = reverse(name, kwargs=kw) if kw else reverse(name)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200,
                         f'{name} ({url}) returned {resp.status_code}')
        return resp

    def test_every_page_renders_for_a_signed_in_member(self):
        self.client.force_login(self.user)
        # Week 1 with no preseason picks bounces home to /preseason/ by design;
        # this is about rendering, not that gate.
        self.user.profile.preseason_submitted = True
        self.user.profile.save()
        for name in self.PAGES:
            with self.subTest(page=name):
                self._ok(name)

    def test_home_renders_in_the_preseason_prompt_state(self):
        # The other half of the band: picks not yet in, so the amber row shows.
        self.client.force_login(self.user)
        session = self.client.session
        session['preseason_deferred'] = True
        session.save()
        self._ok('main:home')

    def test_my_profile_renders_its_textarea(self):
        # The branch that used to raise: ProfileForm.bio is a Textarea.
        self.client.force_login(self.user)
        self.assertIn('<textarea', self._ok('accounts:user_profile').content.decode())

    def test_every_template_compiles(self):
        """Catches the malformed-tag half without needing a route for each page."""
        from pathlib import Path

        from django.conf import settings as dj_settings
        from django.template.loader import get_template

        root = Path(dj_settings.BASE_DIR) / 'templates'
        found = 0
        for path in root.rglob('*.html'):
            rel = path.relative_to(root).as_posix()
            with self.subTest(template=rel):
                get_template(rel)
            found += 1
        self.assertGreater(found, 15, 'template sweep found suspiciously few files')


class HomeSideAndGameIdTests(TestCase):
    """The bug that made auto-grading award every game to the loser.

    `Game.home_team` was documented as "team2 is home" while both writers stored
    "team1 (the favorite) is home". Grading and the venue line believed the help
    text, so on a full week of real 2025 results the nfl_data_py path graded 16 of
    16 games to the losing team and the ESPN path matched none of them at all —
    its ids said '2025_1_DAL_PHI' where the stored ones said '2025_01_DAL_PHI',
    and the abbreviation fallback compared home against away.

    Fixtures, not the network: these must fail loudly in CI, not depend on a
    season's data still being served.
    """

    def setUp(self):
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 1
        self.settings.save()
        self.addCleanup(setattr, scrape, 'grade', scrape.grade)

    def _grade_with(self, rows):
        scrape.grade = lambda **kw: rows
        return do_grade(self.settings, year=2025, week=1)

    def test_home_win_goes_to_team1_when_team1_is_home(self):
        g = make_game(week=1, team1='Philadelphia Eagles', team2='Dallas Cowboys',
                      game_id='2025_01_DAL_PHI', team1_is_home=True)
        self._grade_with([['2025_01_DAL_PHI', 'home', 'PHI', 'DAL']])
        g.refresh_from_db()
        self.assertEqual(g.winner, 'team1')   # PHI were at home and won

    def test_home_win_goes_to_team2_when_team2_is_home(self):
        g = make_game(week=1, team1='Kansas City Chiefs', team2='Los Angeles Chargers',
                      game_id='2025_01_KC_LAC', team1_is_home=False)
        self._grade_with([['2025_01_KC_LAC', 'home', 'LAC', 'KC']])
        g.refresh_from_db()
        self.assertEqual(g.winner, 'team2')   # LAC were at home and won

    def test_away_win_is_the_other_side(self):
        g = make_game(week=1, team1='Cincinnati Bengals', team2='Cleveland Browns',
                      game_id='2025_01_CIN_CLE', team1_is_home=False)
        self._grade_with([['2025_01_CIN_CLE', 'away', 'CLE', 'CIN']])
        g.refresh_from_db()
        self.assertEqual(g.winner, 'team1')   # CIN were away and won

    def test_espn_style_id_matches_a_stored_nflverse_id(self):
        """Unpadded week number must not stop the match."""
        g = make_game(week=1, team1='Philadelphia Eagles', team2='Dallas Cowboys',
                      game_id='2025_01_DAL_PHI', team1_is_home=True)
        graded = self._grade_with([['2025_1_DAL_PHI', 'home', 'PHI', 'DAL']])
        g.refresh_from_db()
        self.assertEqual(graded, 1)
        self.assertEqual(g.winner, 'team1')

    def test_rams_match_across_the_two_spellings(self):
        """nfl_data_py says LA, ESPN says LAR — the same fixture either way."""
        g = make_game(week=1, team1='Los Angeles Rams', team2='San Francisco 49ers',
                      game_id='2026_01_SF_LA', team1_is_home=True)
        graded = self._grade_with([['2026_01_SF_LAR', 'home', 'LAR', 'SF']])
        g.refresh_from_db()
        self.assertEqual(graded, 1)
        self.assertEqual(g.winner, 'team1')

    def test_abbreviation_fallback_when_no_id_matches(self):
        """A hand-entered game has no source id; teams must still match."""
        g = make_game(week=1, team1='Green Bay Packers', team2='Chicago Bears',
                      game_id='', team1_is_home=True)
        graded = self._grade_with([['whatever', 'away', 'GB', 'CHI']])
        g.refresh_from_db()
        self.assertEqual(graded, 1)
        self.assertEqual(g.winner, 'team2')   # CHI were away and won

    def test_unmapped_abbreviations_resolve(self):
        from .teams import team_from_abbrev, canonical_abbrev, make_game_id
        self.assertEqual(team_from_abbrev('LA'), 'Los Angeles Rams')
        self.assertEqual(team_from_abbrev('LAR'), 'Los Angeles Rams')
        self.assertEqual(team_from_abbrev('WSH'), 'Washington Commanders')
        self.assertEqual(team_from_abbrev('WAS'), 'Washington Commanders')
        self.assertEqual(canonical_abbrev('la'), 'LAR')
        # Both sources, one id.
        self.assertEqual(make_game_id(2026, 1, 'SF', 'LA'),
                         make_game_id(2026, '1', 'SF', 'LAR'))

    def test_scraper_never_stores_a_bare_abbreviation(self):
        """'LA' used to land in the database as the team name itself."""
        from .teams import TEAMS
        valid = {t[0] for t in TEAMS}
        rows = [('LA', 'SF', -150, 130, True, '2026_01_SF_LA', None)]
        self.addCleanup(setattr, scrape, 'scrape', scrape.scrape)
        scrape.scrape = lambda **kw: rows
        self.addCleanup(setattr, scrape, 'get_week_type', scrape.get_week_type)
        scrape.get_week_type = lambda *a, **k: 'regular'
        do_scrape_and_publish(self.settings, year=2026)
        for g in Game.objects.filter(week=1):
            self.assertIn(g.team1, valid, f'{g.team1!r} is not a real team name')
            self.assertIn(g.team2, valid, f'{g.team2!r} is not a real team name')

    def test_venue_line_names_the_home_team(self):
        """`/picks/` rendered "@ DAL" for a game played in Philadelphia."""
        import re
        from datetime import datetime as _dt, timezone as _tz

        user = make_member('venue', password='pw', email='v@x.com')
        user.profile.preseason_submitted = True
        user.profile.save()
        self.settings.publish = True
        self.settings.lock_picks = False
        self.settings.save()
        self.client.force_login(user)

        for flag, expected in ((True, 'PHI'), (False, 'DAL')):
            Game.objects.all().delete()
            Game.objects.create(league=default_league(), 
                team1='Philadelphia Eagles', team2='Dallas Cowboys',
                points1=1.0, points2=3.3, team1_is_home=flag, week=1,
                game_id='2025_01_DAL_PHI',
                game_dt=_dt(2025, 9, 5, 0, 20, tzinfo=_tz.utc))
            body = self.client.get('/picks/').content.decode().split('</style>')[-1]
            found = re.findall(r'@\s*([A-Z]{2,4})', body)
            self.assertTrue(found, 'no venue rendered')
            self.assertEqual(found[0], expected,
                             f'team1_is_home={flag} should show @{expected}')


class SlateValidationTests(TestCase):
    """A bad scrape must not reach the league.

    do_scrape_and_publish set `publish = True` unconditionally, so a source
    outage published an empty week and mailed everyone about it, and a week whose
    moneylines had not been posted yet published with every underdog worth the
    same as its favorite.
    """

    def setUp(self):
        from . import auto
        self.auto = auto
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 3
        self.settings.auto_tz = 'UTC'
        self.settings.auto_retry_window_minutes = 360
        self.settings.save()
        self.sunday = datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)

        for name, repl in (
            ('get_first_game_dt', lambda **kw: self.sunday),
            ('get_week_type', lambda *a, **kw: 'regular'),
        ):
            self.addCleanup(setattr, auto.scrape_module, name,
                            getattr(auto.scrape_module, name))
            setattr(auto.scrape_module, name, repl)
        self.addCleanup(setattr, auto, 'make_bot_picks', auto.make_bot_picks)
        auto.make_bot_picks = lambda *a, **kw: None

    def _rows(self, priced=True):
        ml = (150, -170) if priced else (0, 0)
        return [('ATL', 'GB', ml[0], ml[1], True, '2026_03_GB_ATL', self.sunday)]

    def _serve(self, rows, cross=None):
        """Serve `rows` to the real scrape and `cross` to the cross-check."""
        from . import auto
        cross = rows if cross is None else cross
        self.addCleanup(setattr, auto.scrape_module, 'scrape', auto.scrape_module.scrape)
        auto.scrape_module.scrape = (
            lambda **kw: cross if kw.get('api_type') == 'espn' else rows)

    def test_a_week_with_no_moneylines_is_held_back(self):
        self._serve(self._rows(priced=False))
        self.auto.do_scrape_and_publish(self.settings, year=2026)
        self.settings.refresh_from_db()
        self.assertFalse(self.settings.publish)
        self.assertIn('no moneyline', self.settings.auto_last_issue)

    def test_a_good_week_publishes(self):
        self._serve(self._rows())
        self.auto.do_scrape_and_publish(self.settings, year=2026)
        self.settings.refresh_from_db()
        self.assertTrue(self.settings.publish)
        self.assertEqual(self.settings.auto_last_issue, '')

    def test_it_publishes_once_the_retry_window_has_elapsed(self):
        """A permanently degraded source must not stall the season forever."""
        self._serve(self._rows(priced=False))
        self.auto.do_scrape_and_publish(self.settings, year=2026)
        self.settings.refresh_from_db()
        self.assertFalse(self.settings.publish)

        # Backdate the first attempt past the window and tick again.
        self.settings.auto_first_attempt_dt = (
            datetime.now(timezone.utc) - timedelta(minutes=400))
        self.settings.save()
        self.auto.do_scrape_and_publish(self.settings, year=2026)
        self.settings.refresh_from_db()
        self.assertTrue(self.settings.publish, 'should publish after the window')
        self.assertIn('no moneyline', self.settings.auto_last_issue,
                      'and should still say what was wrong')

    def test_a_short_slate_is_caught_by_the_cross_check(self):
        """One source quietly missing games looks fine on its own."""
        short = self._rows()
        full = short + [('CHI', 'DET', 120, -140, True, '2026_03_DET_CHI', self.sunday)]
        self._serve(short, cross=full)
        self.auto.do_scrape_and_publish(self.settings, year=2026)
        self.settings.refresh_from_db()
        self.assertFalse(self.settings.publish)
        self.assertIn('disagree', self.settings.auto_last_issue)

    def test_manual_scrape_publishes_regardless(self):
        """The dashboard button is a person looking at the result."""
        self._serve(self._rows(priced=False))
        self.auto.do_scrape_and_publish(self.settings, year=2026, force=True)
        self.settings.refresh_from_db()
        self.assertTrue(self.settings.publish)


class GameDaySetTests(TestCase):
    """The old from/to range could not express a non-contiguous set of days."""

    def setUp(self):
        self.settings = LeagueSettings.for_league(default_league())

    def _dt(self, day):
        # 2026-09-21 is a Monday, so +day lands on weekday `day`.
        return datetime(2026, 9, 21 + day, 18, 0, tzinfo=timezone.utc)

    def test_sunday_and_monday_without_saturday(self):
        from .auto import _game_day_allowed
        self.settings.scrape_days = '6,0'
        days = self.settings.scrape_day_set()
        self.assertTrue(_game_day_allowed(self._dt(6), days))   # Sunday
        self.assertTrue(_game_day_allowed(self._dt(0), days))   # Monday
        self.assertFalse(_game_day_allowed(self._dt(5), days))  # Saturday
        self.assertFalse(_game_day_allowed(self._dt(3), days))  # Thursday

    def test_blank_means_every_day(self):
        from .auto import _game_day_allowed
        self.settings.scrape_days = ''
        days = self.settings.scrape_day_set()
        for d in range(7):
            self.assertTrue(_game_day_allowed(self._dt(d), days))

    def test_junk_is_ignored_rather_than_raising(self):
        self.settings.scrape_days = '6, x, 9, 0,'
        self.assertEqual(self.settings.scrape_day_set(), {6, 0})


class AutoTickScheduleTests(TestCase):
    """Grade time, the advance toggle, and the end of the season."""

    def setUp(self):
        from . import auto
        self.auto = auto
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.auto_enabled = True
        self.settings.week = 3
        self.settings.publish = True
        self.settings.lock_picks = True
        self.settings.season_last_week = 22
        self.settings.save()

        self.graded = []
        self.advanced = []
        self.addCleanup(setattr, auto, 'do_grade', auto.do_grade)
        self.addCleanup(setattr, auto, 'do_advance_week', auto.do_advance_week)
        auto.do_grade = lambda s, **kw: self.graded.append(1)
        auto.do_advance_week = lambda s: self.advanced.append(1)

    def _game(self, graded):
        return make_game(week=3, graded=graded, winner='team1' if graded else '')

    def test_grading_waits_for_the_grade_time(self):
        self._game(graded=False)
        self.settings.auto_grade_dt = datetime.now(timezone.utc) + timedelta(hours=5)
        self.settings.save()
        self.auto.auto_tick(default_league())
        self.assertEqual(self.graded, [], 'must not grade before the grade time')

    def test_grading_runs_once_the_grade_time_has_passed(self):
        self._game(graded=False)
        self.settings.auto_grade_dt = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.settings.save()
        self.auto.auto_tick(default_league())
        self.assertEqual(len(self.graded), 1)

    def test_advance_is_skipped_when_the_toggle_is_off(self):
        self._game(graded=True)
        self.settings.auto_advance = False
        self.settings.save()
        self.auto.auto_tick(default_league())
        self.assertEqual(self.advanced, [], 'auto_advance off must hold the week')

    def test_advance_runs_when_the_toggle_is_on(self):
        self._game(graded=True)
        self.settings.auto_advance = True
        self.settings.save()
        self.auto.auto_tick(default_league())
        self.assertEqual(len(self.advanced), 1)

    def test_the_final_week_is_scored_then_the_season_stops(self):
        Game.objects.all().delete()
        make_game(week=22, graded=True, winner='team1')
        self.settings.week = 22
        self.settings.auto_advance = True
        self.settings.save()
        self.auto.auto_tick(default_league())
        self.assertEqual(len(self.advanced), 1, 'the last week still gets scored')

    def test_nothing_happens_past_the_end_of_the_season(self):
        """It used to roll into week 23 and mail an empty slate, forever."""
        Game.objects.all().delete()
        self.settings.week = 23
        self.settings.publish = False
        self.settings.auto_scrape_dt = datetime.now(timezone.utc) - timedelta(hours=1)
        self.settings.save()
        self.auto.auto_tick(default_league())
        self.settings.refresh_from_db()
        self.assertFalse(self.settings.publish, 'must not publish past the season')
        self.assertEqual(self.advanced, [])


class AutoSettingsFormTests(TestCase):
    """The new controls have to survive a round trip through the dashboard.

    A field the view never reads looks fine in the form and silently keeps its
    default forever, which is exactly how a control that does nothing ships.
    """

    def setUp(self):
        self.user = make_member('boss', password='pw', email='b@x.com')
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.auto_tz = 'UTC'
        self.settings.save()

    def _post(self, **over):
        data = {
            'save_auto': '1',
            'tz': 'UTC',
            'auto_scrape_weekday': '1',
            'auto_scrape_time': '14:30',
            'scrape_days': ['6', '0'],
            'lock_mode': 'offset',
            'auto_lock_offset_minutes': '20',
            'tick_interval': '300',
            'season_last_week': '18',
            'auto_retry_window_minutes': '120',
            'auto_advance': 'on',
        }
        data.update(over)
        resp = self.client.post('/dashboard/picks/', data)
        self.settings.refresh_from_db()
        return resp

    def test_game_days_round_trip(self):
        self._post()
        self.assertEqual(self.settings.scrape_day_set(), {6, 0})

    def test_all_days_ticked_is_stored_as_no_filter(self):
        self._post(scrape_days=[str(d) for d in range(7)])
        self.assertEqual(self.settings.scrape_days, '')
        self.assertEqual(self.settings.scrape_day_set(), set())

    def test_advance_toggle_off_when_unchecked(self):
        self._post()
        self.assertTrue(self.settings.auto_advance)
        data_without = {'auto_advance': ''}
        # An unchecked box is simply absent from the POST.
        resp = self.client.post('/dashboard/picks/', {
            'save_auto': '1', 'tz': 'UTC',
            'auto_scrape_weekday': '1', 'auto_scrape_time': '14:30',
            'scrape_days': ['6'], 'lock_mode': 'offset',
            'auto_lock_offset_minutes': '20', 'tick_interval': '300',
            'season_last_week': '18', 'auto_retry_window_minutes': '120',
        })
        self.settings.refresh_from_db()
        self.assertFalse(self.settings.auto_advance)

    def test_season_length_and_retry_window_round_trip(self):
        self._post()
        self.assertEqual(self.settings.season_last_week, 18)
        self.assertEqual(self.settings.auto_retry_window_minutes, 120)

    def test_out_of_range_values_are_clamped_not_stored(self):
        self._post(season_last_week='999', auto_retry_window_minutes='-5')
        self.assertEqual(self.settings.season_last_week, 30)
        self.assertEqual(self.settings.auto_retry_window_minutes, 0)

    def test_the_form_renders_the_saved_state_back(self):
        self._post()
        html = self.client.get('/dashboard/picks/').content.decode()
        # Sunday and Monday ticked, Saturday not.
        import re
        for val, want in ((6, True), (0, True), (5, False)):
            m = re.search(r'name="scrape_days" value="%d"([^>]*)>' % val, html)
            self.assertIsNotNone(m, 'day %d checkbox missing' % val)
            self.assertEqual('checked' in m.group(1), want,
                             'day %d checked state wrong' % val)


class ScrapeIsIdempotentTests(TestCase):
    """Re-scraping a week must update the fixture, never store it twice.

    The duplicate check was `Q(game_id=...) | Q(team1=..., team2=...)`. team1 and
    team2 are favorite and underdog, so when a line crosses pick'em the two swap
    places and the ordered comparison stops matching. Any row whose game_id did
    not match exactly - one entered by hand, or stored before the two sources
    agreed on an id format - came back a second time with the teams reversed.
    """

    def setUp(self):
        from . import auto
        self.auto = auto
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 3
        self.settings.save()
        self.kick = datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)

        for name, repl in (('get_first_game_dt', lambda **k: self.kick),
                           ('get_week_type', lambda *a, **k: 'regular')):
            self.addCleanup(setattr, auto.scrape_module, name,
                            getattr(auto.scrape_module, name))
            setattr(auto.scrape_module, name, repl)
        self.addCleanup(setattr, auto, 'make_bot_picks', auto.make_bot_picks)
        auto.make_bot_picks = lambda *a, **k: None
        self.addCleanup(setattr, auto.scrape_module, 'scrape', auto.scrape_module.scrape)

    def _serve(self, rows):
        self.auto.scrape_module.scrape = lambda **k: rows

    def _gb_favored(self):
        return [('Green Bay Packers', 'Atlanta Falcons', -170, 150, True,
                 '2026_03_ATL_GB', self.kick)]

    def _atl_favored(self):
        # The line moved across pick'em: favorite and underdog change places.
        return [('Atlanta Falcons', 'Green Bay Packers', -120, 105, False,
                 '2026_03_ATL_GB', self.kick)]

    def test_rescraping_the_same_week_adds_nothing(self):
        self._serve(self._gb_favored())
        self.auto.scrape_week_games(self.settings, year=2026)
        self.auto.scrape_week_games(self.settings, year=2026)
        self.assertEqual(Game.objects.filter(week=3).count(), 1)

    def test_a_flipped_favorite_updates_rather_than_duplicating(self):
        self._serve(self._gb_favored())
        self.auto.scrape_week_games(self.settings, year=2026)
        self._serve(self._atl_favored())
        report = self.auto.scrape_week_games(self.settings, year=2026)

        self.assertEqual(Game.objects.filter(week=3).count(), 1)
        self.assertEqual(report['updated'], 1)
        self.assertEqual(report['added'], 0)
        g = Game.objects.get(week=3)
        self.assertEqual(g.team1, 'Atlanta Falcons', 'new favorite should be team1')
        self.assertEqual(g.team2, 'Green Bay Packers')

    def test_a_row_with_no_game_id_still_matches(self):
        """A game added by hand on the dashboard carries no source id."""
        Game.objects.create(league=default_league(), week=3, team1='Green Bay Packers',
                            team2='Atlanta Falcons', points1=1.0, points2=2.5,
                            game_id='', game_dt=self.kick, team1_is_home=True)
        self._serve(self._atl_favored())
        self.auto.scrape_week_games(self.settings, year=2026)
        self.assertEqual(Game.objects.filter(week=3).count(), 1)

    def test_an_old_format_game_id_still_matches(self):
        """Ids stored before the two sources agreed on a format."""
        Game.objects.create(league=default_league(), week=3, team1='Green Bay Packers',
                            team2='Atlanta Falcons', points1=1.0, points2=2.5,
                            game_id='2026_3_ATL_GB', game_dt=self.kick,
                            team1_is_home=True)
        self._serve(self._atl_favored())
        self.auto.scrape_week_games(self.settings, year=2026)
        self.assertEqual(Game.objects.filter(week=3).count(), 1)

    def test_a_flexed_kickoff_updates_the_time_and_does_not_duplicate(self):
        """Kickoff time is not part of the key: flexed games are still the game."""
        self._serve(self._gb_favored())
        self.auto.scrape_week_games(self.settings, year=2026)
        moved = datetime(2026, 9, 28, 0, 20, tzinfo=timezone.utc)
        self._serve([('Green Bay Packers', 'Atlanta Falcons', -170, 150, True,
                      '2026_03_ATL_GB', moved)])
        self.auto.scrape_week_games(self.settings, year=2026)

        self.assertEqual(Game.objects.filter(week=3).count(), 1)
        self.assertEqual(Game.objects.get(week=3).game_dt, moved)

    def test_points_are_frozen_once_picks_lock(self):
        """Members picked against the numbers they were shown."""
        self._serve(self._gb_favored())
        self.auto.scrape_week_games(self.settings, year=2026)
        before = Game.objects.get(week=3)
        old_points = (before.team1, before.points2)

        self.settings.lock_picks = True
        self.settings.save()
        self._serve(self._atl_favored())
        self.auto.scrape_week_games(self.settings, year=2026)

        after = Game.objects.get(week=3)
        self.assertEqual((after.team1, after.points2), old_points,
                         'a locked week must not be rescored by a re-scrape')
        self.assertEqual(Game.objects.filter(week=3).count(), 1)

    def test_the_two_teams_meeting_again_later_is_a_separate_game(self):
        """Division rivals play twice; the second meeting is its own row."""
        self._serve(self._gb_favored())
        self.auto.scrape_week_games(self.settings, year=2026)
        self.settings.week = 14
        self.settings.save()
        self._serve([('Green Bay Packers', 'Atlanta Falcons', -150, 130, True,
                      '2026_14_ATL_GB', datetime(2026, 12, 6, 18, 0, tzinfo=timezone.utc))])
        self.auto.scrape_week_games(self.settings, year=2026)

        self.assertEqual(Game.objects.filter(week=3).count(), 1)
        self.assertEqual(Game.objects.filter(week=14).count(), 1)

    def test_match_existing_ignores_team_order(self):
        """The matcher itself, with no game_id to short-circuit on."""
        g = Game.objects.create(league=default_league(), week=3, team1='Green Bay Packers',
                                team2='Atlanta Falcons', points1=1.0, points2=2.5,
                                game_id='', team1_is_home=True)
        # Same pair, either way round.
        self.assertEqual(
            Game.match_existing(default_league(), 3, 'Green Bay Packers', 'Atlanta Falcons'), g)
        self.assertEqual(
            Game.match_existing(default_league(), 3, 'Atlanta Falcons', 'Green Bay Packers'), g)
        # A different fixture must not match.
        self.assertIsNone(
            Game.match_existing(default_league(), 3, 'Chicago Bears', 'Atlanta Falcons'))
        # Nor the same fixture in another week.
        self.assertIsNone(
            Game.match_existing(default_league(), 4, 'Green Bay Packers', 'Atlanta Falcons'))

    def test_match_existing_matches_across_id_spellings(self):
        """nfl_data_py's LA and ESPN's LAR are the same fixture."""
        g = Game.objects.create(league=default_league(), week=3, team1='Los Angeles Rams',
                                team2='San Francisco 49ers', points1=1.0,
                                points2=2.1, game_id='2026_03_SF_LA',
                                team1_is_home=True)
        self.assertEqual(
            Game.match_existing(default_league(), 3, 'Los Angeles Rams', 'San Francisco 49ers',
                                '2026_3_SF_LAR'), g)


class ManualScrapeWeekTests(TestCase):
    """The dashboard's Scrape button stored under `settings.week`, not the week
    it was asked to scrape. Pulling week 1 while the site sat on week 2 filed
    week 1's fixtures under week 2 — which also defeats the dedup key, since that
    is scoped by week."""

    def setUp(self):
        self.user = make_member('sc', password='pw', email='s@x.com')
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 2
        self.settings.publish = False
        self.settings.save()

        self.addCleanup(setattr, scrape, 'scrape', scrape.scrape)
        self.kick = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)
        scrape.scrape = lambda **kw: [
            ('Green Bay Packers', 'Atlanta Falcons', -170, 150, True,
             '2026_01_ATL_GB', self.kick)]

    def test_games_land_in_the_week_that_was_scraped(self):
        self.client.post('/dashboard/picks/', {
            'scrape': '1', 'scrape_week': '1',
            'scrape_api': 'nfl_data_py', 'grade_api': 'espn',
            'scrape_year': '2026',
        })
        self.assertEqual(Game.objects.filter(week=1).count(), 1)
        self.assertEqual(Game.objects.filter(week=2).count(), 0)

    def test_scraping_another_week_leaves_the_live_lock_alone(self):
        self.settings.auto_lock_dt = None
        self.settings.first_game_dt = None
        self.settings.save()
        self.client.post('/dashboard/picks/', {
            'scrape': '1', 'scrape_week': '1',
            'scrape_api': 'nfl_data_py', 'grade_api': 'espn',
            'scrape_year': '2026',
        })
        self.settings.refresh_from_db()
        self.assertIsNone(self.settings.first_game_dt,
                          "a future week's kickoff must not become this week's lock")


class GradingStartsAtFirstKickoffTests(TestCase):
    """Grading is not scheduled by hand — it begins when the first game does.

    It used to run on every tick from the moment picks locked, polling the source
    all Sunday afternoon for results that could not exist yet. A configured
    weekday and time fixed that but introduced its own problem: the slate moves
    (flex scheduling), and a hand-set time just sits there being wrong.
    """

    def setUp(self):
        from . import auto
        self.auto = auto
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 3
        self.settings.save()
        self.thursday = datetime(2026, 9, 24, 23, 15, tzinfo=timezone.utc)
        self.sunday = datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)
        self.addCleanup(setattr, auto, 'make_bot_picks', auto.make_bot_picks)
        auto.make_bot_picks = lambda *a, **k: None
        self.addCleanup(setattr, auto.scrape_module, 'get_first_game_dt',
                        auto.scrape_module.get_first_game_dt)
        auto.scrape_module.get_first_game_dt = lambda **k: None

    def test_publishing_arms_grading_at_the_first_kickoff(self):
        make_game(week=3, game_dt=self.sunday)
        make_game(week=3, team1='Buffalo Bills', team2='Miami Dolphins',
                  game_dt=self.thursday)
        self.auto.publish_week(self.settings, year=2026)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.auto_grade_dt, self.thursday,
                         'grading starts with the earliest game in the slate')

    def test_it_follows_the_slate_not_the_calendar(self):
        """A Sunday-only league grades from its Sunday game, not the Thursday
        nighter it never picked."""
        make_game(week=3, game_dt=self.sunday)
        self.auto.publish_week(self.settings, year=2026)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.auto_grade_dt, self.sunday)

    def test_grading_does_not_run_before_the_first_game(self):
        self.settings.auto_enabled = True
        self.settings.publish = True
        self.settings.lock_picks = True
        self.settings.auto_grade_dt = datetime.now(timezone.utc) + timedelta(hours=3)
        self.settings.save()
        make_game(week=3, graded=False)

        calls = []
        self.addCleanup(setattr, self.auto, 'do_grade', self.auto.do_grade)
        self.auto.do_grade = lambda s, **kw: calls.append(1)
        self.auto.auto_tick(default_league())
        self.assertEqual(calls, [], 'nothing can be graded before kickoff')


class WeeklyEmailShapeTests(TestCase):
    """One mail a week: intro, recap, ballot - in that order.

    The recap used to go out on its own when a week advanced, and the ballot sat
    above it in the picks-are-live mail. That was two emails a week, with the
    longest section buried in the middle of one of them.
    """

    def setUp(self):
        from . import email_utils
        self.email_utils = email_utils
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 3
        self.settings.weekly_recap = 'RECAP TEXT HERE'
        self.settings.weekly_intro = 'INTRO TEXT HERE'
        self.settings.save()
        make_game(week=3)
        make_member('m1', email='m1@x.com')

        self.captured = {}
        self.addCleanup(setattr, email_utils, 'record_site_email',
                        email_utils.record_site_email)
        email_utils.record_site_email = lambda *a, **kw: self.captured.update(kw)
        # `outbound_suppressed` is TESTING-gated so the suite cannot post real
        # mail. Lifted here because every transport below is stubbed, and the
        # point of these tests is the body that gets built.
        self.addCleanup(setattr, email_utils, 'outbound_suppressed',
                        email_utils.outbound_suppressed)
        email_utils.outbound_suppressed = lambda: False
        self.addCleanup(setattr, email_utils, 'smtp_ready', email_utils.smtp_ready)
        email_utils.smtp_ready = lambda: True
        self.addCleanup(setattr, email_utils, 'send_via_mailbox',
                        email_utils.send_via_mailbox)
        email_utils.send_via_mailbox = lambda *a, **kw: (True, '')

    def _body(self):
        self.email_utils.send_picks_published_email(self.settings)
        return self.captured.get('body', '')

    def test_sections_appear_in_order(self):
        body = self._body()
        self.assertIn('INTRO TEXT HERE', body)
        self.assertIn('RECAP TEXT HERE', body)
        self.assertLess(body.index('INTRO TEXT HERE'), body.index('RECAP TEXT HERE'),
                        'the intro belongs above the recap')

    def test_the_ballot_is_last(self):
        self.settings.email_ballot = True
        self.settings.save()
        self.addCleanup(setattr, self.email_utils, 'picks_address',
                        self.email_utils.picks_address)
        self.email_utils.picks_address = lambda: 'picks@x.com'
        body = self._body()
        # build_ballot() carries its own rule. A section header here as well put
        # two dividers back to back with nothing between them.
        self.assertNotIn('Your Picks', body)
        self.assertEqual(body.count('Reply with your picks'), 1)
        self.assertGreater(body.index('Reply with your picks'),
                           body.index('RECAP TEXT HERE'),
                           'the ballot is the longest part; it goes last')

    def test_a_blank_intro_omits_the_section_entirely(self):
        self.settings.weekly_intro = '   '
        self.settings.save()
        body = self._body()
        self.assertIn('RECAP TEXT HERE', body)
        self.assertNotIn('INTRO', body)

    def test_the_recap_switch_drops_only_the_recap(self):
        self.settings.email_recap = False
        self.settings.save()
        body = self._body()
        self.assertNotIn('RECAP TEXT HERE', body)
        self.assertIn('INTRO TEXT HERE', body)

    def test_advancing_clears_the_intro(self):
        from . import auto
        self.addCleanup(setattr, auto, 'build_recap', auto.build_recap)
        auto.build_recap = lambda league, week: None
        auto.do_advance_week(self.settings)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.weekly_intro, '',
                         "last week's note must not go out with this week's games")
        self.assertEqual(self.settings.reminder_sent_week, 0)


class PickReminderTests(TestCase):
    """Goes only to people whose ballot is short, and only once."""

    def setUp(self):
        from . import email_utils
        self.email_utils = email_utils
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 3
        self.settings.email_reminder = True
        self.settings.save()

        self.g1 = make_game(week=3)
        self.g2 = make_game(week=3, team1='Buffalo Bills', team2='Miami Dolphins')

        self.done = make_member('done', email='d@x.com')
        self.partial = make_member('partial', email='p@x.com')
        self.none = make_member('none', email='n@x.com')
        for g in (self.g1, self.g2):
            Pick.objects.create(user=self.done, game=g, choice='team1')
        Pick.objects.create(user=self.partial, game=self.g1, choice='team1')

        # `outbound_suppressed` is TESTING-gated so the suite cannot post real
        # mail. Lifted here because every transport below is stubbed, and the
        # point of these tests is the body that gets built.
        self.addCleanup(setattr, email_utils, 'outbound_suppressed',
                        email_utils.outbound_suppressed)
        email_utils.outbound_suppressed = lambda: False
        self.addCleanup(setattr, email_utils, 'smtp_ready', email_utils.smtp_ready)
        email_utils.smtp_ready = lambda: True
        self.addCleanup(setattr, email_utils, 'send_via_mailbox',
                        email_utils.send_via_mailbox)
        email_utils.send_via_mailbox = lambda *a, **kw: (True, '')
        self.addCleanup(setattr, email_utils, 'record_site_email',
                        email_utils.record_site_email)
        email_utils.record_site_email = lambda *a, **kw: None

    def test_it_targets_incomplete_ballots_not_just_empty_ones(self):
        names = {u.username for u, _, _ in
                 self.email_utils.members_missing_picks(default_league(), 3)}
        self.assertEqual(names, {'partial', 'none'},
                         'a partial ballot scores nothing, so it counts as missing')

    def test_it_sends_once_per_week(self):
        n = self.email_utils.send_pick_reminder_email(self.settings)
        self.assertEqual(n, 2)
        self.settings.refresh_from_db()
        again = self.email_utils.send_pick_reminder_email(self.settings)
        self.assertEqual(again, 0, 'a tick every 5 minutes must not re-send it')

    def test_the_switch_turns_it_off(self):
        self.settings.email_reminder = False
        self.settings.save()
        self.assertEqual(
            self.email_utils.send_pick_reminder_email(self.settings), 0)

    def test_bots_are_never_reminded(self):
        bot = make_member('bot', email='b@x.com')
        bot.profile.is_bot = True
        bot.profile.save()
        names = {u.username for u, _, _ in
                 self.email_utils.members_missing_picks(default_league(), 3)}
        self.assertNotIn('bot', names)

    def test_the_tick_fires_it_inside_the_window(self):
        from . import auto
        self.settings.auto_enabled = True
        self.settings.publish = True
        self.settings.lock_picks = False
        self.settings.auto_lock_dt = datetime.now(timezone.utc) + timedelta(hours=2)
        self.settings.reminder_hours_before_lock = 24
        self.settings.save()

        calls = []
        self.addCleanup(setattr, auto, 'do_grade', auto.do_grade)
        auto.do_grade = lambda s, **kw: None
        self.addCleanup(setattr, self.email_utils, 'send_pick_reminder_email',
                        self.email_utils.send_pick_reminder_email)
        self.email_utils.send_pick_reminder_email = lambda s: calls.append(1)
        auto.auto_tick(default_league())
        self.assertEqual(len(calls), 1)

    def test_the_tick_holds_off_outside_the_window(self):
        from . import auto
        self.settings.auto_enabled = True
        self.settings.publish = True
        self.settings.lock_picks = False
        self.settings.auto_lock_dt = datetime.now(timezone.utc) + timedelta(days=5)
        self.settings.reminder_hours_before_lock = 24
        self.settings.save()

        calls = []
        self.addCleanup(setattr, self.email_utils, 'send_pick_reminder_email',
                        self.email_utils.send_pick_reminder_email)
        self.email_utils.send_pick_reminder_email = lambda s: calls.append(1)
        auto.auto_tick(default_league())
        self.assertEqual(calls, [], 'five days out is not "closing soon"')


class RecapStatsTests(TestCase):
    """The recap prompt gets angles, not a dump of every pick.

    It used to hand the model sixteen lines of `TEAM vs TEAM - winner: X | picks:
    alice->team1, ...` and leave it to notice that one game caught everyone out.
    It mostly did not, so the recaps read like a results table in prose.
    """

    def setUp(self):
        from . import recap_stats
        self.stats = recap_stats
        LeagueSettings.for_league(default_league())
        self.users = [make_member(n, email=f'{n}@x.com')
                      for n in ('alice', 'bob', 'carol', 'dave')]

    def _game(self, **kw):
        kw.setdefault('graded', True)
        return make_game(week=3, **kw)

    def _pick_all(self, game, choice, users=None):
        for u in (users or self.users):
            Pick.objects.create(user=u, game=game, choice=choice)

    def test_nothing_gradeable_yields_nothing(self):
        self._game(graded=False, winner='')
        lines, ranked = self.stats.summary(default_league(), 3)
        self.assertEqual(lines, [])
        self.assertIsNone(ranked)

    def test_a_game_nobody_got_right_is_called_out(self):
        g = self._game(winner='team2')
        self._pick_all(g, 'team1')
        lines, _ = self.stats.summary(default_league(), 3)
        self.assertTrue(any('NOBODY SAW IT' in ln for ln in lines))

    def test_a_game_everyone_got_right_is_called_out(self):
        g = self._game(winner='team1')
        self._pick_all(g, 'team1')
        lines, _ = self.stats.summary(default_league(), 3)
        self.assertTrue(any('EVERYONE GOT IT' in ln for ln in lines))

    def test_the_trap_line_does_not_repeat_the_wipeout(self):
        """Both fired on the same game and said the same thing twice."""
        g = self._game(winner='team2')
        self._pick_all(g, 'team1')
        lines, _ = self.stats.summary(default_league(), 3)
        self.assertFalse(any('TRAP GAME' in ln for ln in lines))

    def test_a_perfect_week_is_flagged(self):
        g1 = self._game(winner='team1')
        g2 = self._game(winner='team1', team1='Buffalo Bills',
                        team2='Miami Dolphins')
        self._pick_all(g1, 'team1', [self.users[0]])
        self._pick_all(g2, 'team1', [self.users[0]])
        lines, _ = self.stats.summary(default_league(), 3)
        self.assertTrue(any('PERFECT WEEK' in ln for ln in lines))

    def test_an_incomplete_ballot_is_flagged(self):
        g1 = self._game(winner='team1')
        self._game(winner='team1', team1='Buffalo Bills', team2='Miami Dolphins')
        self._pick_all(g1, 'team1')
        lines, _ = self.stats.summary(default_league(), 3)
        self.assertTrue(any('INCOMPLETE BALLOTS' in ln for ln in lines))

    def test_standings_movement_needs_a_prior_leaderboard(self):
        g = self._game(winner='team1')
        self._pick_all(g, 'team1')
        lines, _ = self.stats.summary(default_league(), 3)
        self.assertFalse(any('OVERALL LEADER' in ln for ln in lines),
                         'no snapshot to compare against yet')

        WeeklyLeaderboard.objects.create(league=default_league(), week=3, entries=[
            {'username': 'alice', 'score': 10.0},
            {'username': 'bob', 'score': 9.5},
        ])
        lines, _ = self.stats.summary(default_league(), 3)
        self.assertTrue(any('OVERALL LEADER' in ln for ln in lines))
        self.assertTrue(any('TIGHT RACE' in ln for ln in lines))

    def test_the_data_block_carries_angles_and_the_table(self):
        g = self._game(winner='team1')
        self._pick_all(g, 'team1')
        block, ranked = self.stats.data_block(default_league(), 3)
        self.assertIn('things worth writing about', block)
        self.assertIn('Points scored this week', block)
        self.assertTrue(ranked)
        # The old per-game pick dump is gone.
        self.assertNotIn('->team1', block)
        self.assertNotIn('picks:', block)


class IntroLibraryTests(TestCase):
    """Named, editable intros with a {week} placeholder.

    The intro is the one part of the weekly mail nobody can automate, but most
    weeks it says one of a handful of things - so they are saved, named and
    reusable rather than retyped.
    """

    def setUp(self):
        from .models import IntroTemplate
        self.IntroTemplate = IntroTemplate
        self.user = make_member('boss2', password='pw', email='b@x.com')
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 7
        self.settings.save()
        self.tpl = IntroTemplate.objects.create(league=default_league(), 
            name='Test intro', body='Week {week} is live. Push on.')

    def _post(self, **data):
        return self.client.post('/dashboard/emails/', data)

    def test_the_seeded_library_survives_migration(self):
        # The starter set ships in a data migration, so a fresh database has it.
        self.assertGreaterEqual(self.IntroTemplate.objects.count(), 10)
        self.assertTrue(
            self.IntroTemplate.objects.filter(name='Season opener').exists())

    def test_using_one_copies_its_raw_body(self):
        self._post(use_intro=self.tpl.pk)
        self.settings.refresh_from_db()
        # Raw, with the placeholder intact - substitution happens at send time so
        # the text stays correct if it is reused in another week.
        self.assertEqual(self.settings.weekly_intro, 'Week {week} is live. Push on.')

    def test_the_placeholder_resolves_when_the_mail_is_built(self):
        from . import email_utils
        self.settings.weekly_intro = 'Week {week} is live.'
        self.settings.save()
        make_game(week=7)
        make_member('m9', email='m9@x.com')

        captured = {}
        self.addCleanup(setattr, email_utils, 'record_site_email',
                        email_utils.record_site_email)
        email_utils.record_site_email = lambda *a, **kw: captured.update(kw)
        self.addCleanup(setattr, email_utils, 'outbound_suppressed',
                        email_utils.outbound_suppressed)
        email_utils.outbound_suppressed = lambda: False
        self.addCleanup(setattr, email_utils, 'smtp_ready', email_utils.smtp_ready)
        email_utils.smtp_ready = lambda: True
        self.addCleanup(setattr, email_utils, 'send_via_mailbox',
                        email_utils.send_via_mailbox)
        email_utils.send_via_mailbox = lambda *a, **kw: (True, '')

        email_utils.send_picks_published_email(self.settings)
        self.assertIn('Week 7 is live.', captured.get('body', ''))
        self.assertNotIn('{week}', captured.get('body', ''))

    def test_a_stray_brace_does_not_raise(self):
        """The text is hand-edited; format() would blow up mid-send."""
        tpl = self.IntroTemplate.objects.create(league=default_league(), 
            name='Braces', body='Good luck {everyone} :{ week {week}')
        self.assertEqual(tpl.render(4), 'Good luck {everyone} :{ week 4')

    def test_creating_editing_and_deleting(self):
        self._post(save_template='1', tpl_name='New one', tpl_body='Body {week}')
        made = self.IntroTemplate.objects.get(name='New one')
        self.assertEqual(made.body, 'Body {week}')

        self._post(save_template='1', tpl_id=made.pk,
                   tpl_name='Renamed', tpl_body='Changed')
        made.refresh_from_db()
        self.assertEqual((made.name, made.body), ('Renamed', 'Changed'))

        self._post(delete_template=made.pk)
        self.assertFalse(self.IntroTemplate.objects.filter(pk=made.pk).exists())

    def test_duplicate_names_are_refused(self):
        self._post(save_template='1', tpl_name='Season opener', tpl_body='Other text')
        self.assertEqual(
            self.IntroTemplate.objects.filter(name='Season opener').count(), 1)

    def test_an_empty_template_is_refused(self):
        before = self.IntroTemplate.objects.count()
        self._post(save_template='1', tpl_name='', tpl_body='')
        self.assertEqual(self.IntroTemplate.objects.count(), before)

    def test_the_page_lists_them_and_previews_the_placeholder(self):
        self.settings.weekly_intro = 'Week {week} is live.'
        self.settings.save()
        html = self.client.get('/dashboard/emails/').content.decode()
        self.assertIn('Test intro', html)
        self.assertIn('Season opener', html)   # from the seeded set
        # The "Reads as:" line is gated on the placeholder being present, which
        # is a string-literal `in` test inside the template.
        self.assertIn('Reads as:', html)
        self.assertIn('Week 7 is live.', html)

    def test_no_preview_line_without_a_placeholder(self):
        self.settings.weekly_intro = 'Nothing dynamic here.'
        self.settings.save()
        html = self.client.get('/dashboard/emails/').content.decode()
        self.assertNotIn('Reads as:', html)


class IntroByEmailTests(TestCase):
    """Mail to the +intro address becomes this week's intro.

    Same mailbox as everything else - Gmail delivers `user+intro@` to `user@` and
    keeps the tag in the headers - so the league still needs no mailing list.
    """

    def setUp(self):
        from django.conf import settings as dj
        from . import inbound_email, email_utils
        self.inbound = inbound_email
        self.email_utils = email_utils
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 5
        self.settings.save()

        self.addCleanup(setattr, dj, 'SMTP_USER', getattr(dj, 'SMTP_USER', ''))
        dj.SMTP_USER = 'league@gmail.com'

        self.boss = make_member('boss3', email='boss@x.com')
        self.boss.profile.email_posts_enabled = True
        self.boss.profile.save()
        self.member = make_member('member3', email='member@x.com')

        # Authentication is checked separately; these tests are about routing.
        self.addCleanup(setattr, inbound_email, '_auth_ok', inbound_email._auth_ok)
        inbound_email._auth_ok = lambda msg: (True, 'stubbed')
        self.addCleanup(setattr, email_utils, 'smtp_ready', email_utils.smtp_ready)
        email_utils.smtp_ready = lambda: False   # no confirmation attempts

    def _msg(self, to, sender='boss@x.com', body='Four weeks left.', mid='<a@b>'):
        return (f'Message-ID: {mid}\r\n'
                f'From: Boss <{sender}>\r\n'
                f'To: {to}\r\n'
                f'Subject: Intro\r\n'
                f'Date: Mon, 5 Oct 2026 10:00:00 +0000\r\n'
                f'Content-Type: text/plain; charset="utf-8"\r\n'
                f'\r\n{body}\r\n').encode()

    def test_it_becomes_this_weeks_intro(self):
        obj, reason = self.inbound.ingest_message(
            self._msg('league+intro@gmail.com'))
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.weekly_intro, 'Four weeks left.')
        self.assertIn('intro set', reason)
        self.assertIsNone(obj, 'an intro is not a feed post')

    def test_it_is_not_relayed_to_the_league(self):
        before = LeagueEmail.objects.count()
        self.inbound.ingest_message(self._msg('league+intro@gmail.com'))
        self.assertEqual(LeagueEmail.objects.count(), before,
                         'the intro goes out inside the weekly mail, not on its own')

    def test_a_member_without_posting_rights_cannot_set_it(self):
        self.inbound.ingest_message(
            self._msg('league+intro@gmail.com', sender='member@x.com'))
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.weekly_intro, '',
                         'setting the intro is the same trust level as publishing')

    def test_plain_mail_is_unaffected(self):
        self.inbound.ingest_message(
            self._msg('league@gmail.com', body='Just a note.'))
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.weekly_intro, '')

    def test_the_placeholder_survives_the_round_trip(self):
        self.inbound.ingest_message(
            self._msg('league+intro@gmail.com', body='Week {week} is live.'))
        self.settings.refresh_from_db()
        # Stored raw; substituted when the mail is built.
        self.assertEqual(self.settings.weekly_intro, 'Week {week} is live.')

    def test_it_is_deduped_like_everything_else(self):
        self.inbound.ingest_message(self._msg('league+intro@gmail.com'))
        self.settings.weekly_intro = 'edited by hand'
        self.settings.save()
        _, reason = self.inbound.ingest_message(self._msg('league+intro@gmail.com'))
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.weekly_intro, 'edited by hand',
                         'a re-poll of the same message must not overwrite an edit')
        self.assertIn('already ingested', reason)

    def test_the_delivered_to_header_is_enough(self):
        """Clients rewrite the visible To; the envelope keeps the tag."""
        raw = (b'Message-ID: <c@d>\r\n'
               b'From: Boss <boss@x.com>\r\n'
               b'To: league@gmail.com\r\n'
               b'Delivered-To: league+intro@gmail.com\r\n'
               b'Subject: Intro\r\n'
               b'Date: Mon, 5 Oct 2026 10:00:00 +0000\r\n'
               b'Content-Type: text/plain; charset="utf-8"\r\n'
               b'\r\nFrom the envelope.\r\n')
        self.inbound.ingest_message(raw)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.weekly_intro, 'From the envelope.')


class ProfilePageTests(TestCase):
    """The profile form renders its own fields now.

    It used to loop the form and sniff each widget to decide how to draw it,
    which is how `field.field.widget.__class__.__name__` got in — illegal in a
    Django template — and the page raised on every load. Five known fields do not
    need introspection.
    """

    def setUp(self):
        self.user = make_member('pf', password='pw', email='pf@x.com')
        self.client.force_login(self.user)

    def test_every_field_renders(self):
        html = self.client.get('/userprofile/').content.decode()
        for name in ('real_name', 'email', 'favorite_team', 'bio', 'email_weekly', 'email_reminder'):
            with self.subTest(field=name):
                self.assertIn(f'name="{name}"', html)
        self.assertIn('<textarea', html)

    def test_saving_round_trips(self):
        resp = self.client.post('/userprofile/', {
            'real_name': 'Real Name',
            'email': 'new@x.com',
            'favorite_team': 'Chicago Bears',
            'bio': 'A short bio.',
            'email_weekly': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'new@x.com')
        self.assertEqual(self.user.profile.real_name, 'Real Name')
        self.assertEqual(self.user.profile.favorite_team, 'Chicago Bears')
        self.assertEqual(self.user.profile.bio, 'A short bio.')
        self.assertTrue(self.user.profile.email_weekly)
        self.assertFalse(self.user.profile.email_reminder, 'an unticked box is an opt-out')

    def test_the_selected_team_is_marked_selected(self):
        self.user.profile.favorite_team = 'Green Bay Packers'
        self.user.profile.save()
        html = self.client.get('/userprofile/').content.decode()
        self.assertIn('value="Green Bay Packers" selected', html)

    def test_display_helpers(self):
        self.assertEqual(self.user.profile.display_name, 'pf')
        self.user.profile.real_name = 'Someone'
        self.assertEqual(self.user.profile.display_name, 'Someone')
        self.user.profile.favorite_team = 'Green Bay Packers'
        self.assertEqual(self.user.profile.favorite_team_abbrev, 'GB')


class MembersPageTests(TestCase):
    """The roster: who is in the league, when they joined, what they wrote."""

    def setUp(self):
        self.user = make_member('viewer', password='pw', email='v@x.com')
        self.client.force_login(self.user)
        self.mate = make_member('mate', email='m@x.com')
        self.mate.profile.bio = 'Long-suffering Bears fan.'
        self.mate.profile.real_name = 'Team Mate'
        self.mate.profile.favorite_team = 'Chicago Bears'
        self.mate.profile.save()
        self.bot = make_member('abot', email='')
        self.bot.profile.is_bot = True
        self.bot.profile.save()

    def test_it_lists_names_bios_and_join_dates(self):
        html = self.client.get('/members/').content.decode()
        self.assertIn('Team Mate', html)
        self.assertIn('Long-suffering Bears fan.', html)
        self.assertIn(self.mate.date_joined.strftime('%b %Y'), html)

    def test_a_member_without_a_bio_just_omits_it(self):
        """The row carries their team and preseason picks either way, so an
        explicit "no bio" line was noise rather than information."""
        html = self.client.get('/members/').content.decode()
        self.assertNotIn('No bio yet.', html)
        self.assertIn('viewer', html)

    def test_bots_are_folded_into_one_line(self):
        html = self.client.get('/members/').content.decode()
        self.assertNotIn('abot', html, 'a bot is not a member row')
        self.assertIn('1 bot also play', html)
        self.assertGreater(html.index('also play'), html.index('mate'),
                           'the bot line sits under the roster')

    def test_bots_do_not_show_a_favourite_team(self):
        """The field has a default, so every bot claimed to support Arizona."""
        resp = self.client.get('/members/')
        self.assertEqual([m['username'] for m in resp.context['members'] if m['username'] == 'abot'], [])

    def test_it_needs_a_login(self):
        self.client.logout()
        resp = self.client.get('/members/')
        self.assertEqual(resp.status_code, 302)

    def test_names_are_not_links(self):
        """Everything the profile page showed is on the row now, so clicking
        through only cost a page load to read the same facts."""
        html = self.client.get('/members/').content.decode()
        self.assertNotIn('/profile/mate/', html)

    def test_the_favourite_team_is_spelled_out(self):
        self.assertIn('Chicago Bears', self.client.get('/members/').content.decode())

    def test_preseason_picks_are_shown_once_submitted(self):
        p = self.mate.profile
        p.preseason_submitted = True
        p.big_loser = 'Carolina Panthers'
        p.nfc_champ = 'Philadelphia Eagles'
        p.afc_champ = 'Kansas City Chiefs'
        p.superbowl_winner = 'Philadelphia Eagles'
        p.save()
        html = self.client.get('/members/').content.decode()
        for team in ('Carolina Panthers', 'Philadelphia Eagles', 'Kansas City Chiefs'):
            with self.subTest(team=team):
                self.assertIn(team, html)

    def test_unsubmitted_preseason_is_not_passed_off_as_picks(self):
        """Every preseason field has a team as its default, so an untouched
        profile would otherwise claim four confident picks nobody made."""
        self.assertFalse(self.mate.profile.preseason_submitted)
        html = self.client.get('/members/').content.decode()
        self.assertIn('Not in yet', html)
        # Arizona is the default for three of the four fields.
        start = html.index('Team Mate')
        end = html.find('class="mb-row', start)
        block = html[start:end if end > 0 else start + 900]
        self.assertNotIn('Arizona Cardinals', block)


class CompetitionRankingTests(TestCase):
    """Ties share the best place, and the next score skips the tied slots.

    100, 100, 100, 90, 85 -> 1, 1, 1, 4, 5. The site used to number rows with
    `enumerate()` and `forloop.counter`, which gave tied players different places
    for the same score - so someone could read 2nd on the home page and 3rd in
    their own pick history off identical numbers.
    """

    def test_three_tied_at_the_top(self):
        from .rankings import competition_ranks
        ranks = competition_ranks([('a', 100), ('b', 100), ('c', 100),
                                   ('d', 90), ('e', 85)])
        self.assertEqual([ranks[n] for n in 'abcde'], [1, 1, 1, 4, 5])

    def test_a_tie_in_the_middle(self):
        from .rankings import competition_ranks
        ranks = competition_ranks([('a', 100), ('b', 90), ('c', 90), ('d', 80)])
        self.assertEqual([ranks[n] for n in 'abcd'], [1, 2, 2, 4])

    def test_a_tie_at_the_bottom(self):
        from .rankings import competition_ranks
        ranks = competition_ranks([('a', 10), ('b', 5), ('c', 5)])
        self.assertEqual([ranks[n] for n in 'abc'], [1, 2, 2])

    def test_everyone_tied(self):
        from .rankings import competition_ranks
        ranks = competition_ranks([('a', 0), ('b', 0), ('c', 0)])
        self.assertEqual(sorted(ranks.values()), [1, 1, 1])

    def test_it_sorts_for_you(self):
        """Callers cannot get it half right by passing an ascending list."""
        from .rankings import competition_ranks
        ranks = competition_ranks([('low', 1), ('high', 99), ('mid', 50)])
        self.assertEqual(ranks['high'], 1)
        self.assertEqual(ranks['low'], 3)

    def test_empty(self):
        from .rankings import competition_ranks
        self.assertEqual(competition_ranks([]), {})

    def test_rank_rows_attaches_the_rank(self):
        from .rankings import rank_rows
        rows = rank_rows([{'username': 'a', 'score': 5},
                          {'username': 'b', 'score': 5},
                          {'username': 'c', 'score': 1}])
        self.assertEqual([r['rank'] for r in rows], [1, 1, 3])


class TiedLeaderboardTests(TestCase):
    """The tie has to survive all the way to the page."""

    def setUp(self):
        self.tied = []
        for name, score in (('alpha', 10.0), ('bravo', 10.0),
                            ('charlie', 10.0), ('delta', 4.0)):
            u = make_member(name, password='pw', email=f'{name}@x.com')
            u.profile.score = score
            u.profile.preseason_submitted = True
            u.profile.save()
            self.tied.append(u)
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 2
        self.settings.save()
        self.client.force_login(self.tied[0])

    def test_the_home_leaderboard_shares_the_place(self):
        resp = self.client.get('/home/')
        board = resp.context['leaderboard']
        ranks = {e['username']: e['rank'] for e in board}
        self.assertEqual(ranks['alpha'], 1)
        self.assertEqual(ranks['bravo'], 1)
        self.assertEqual(ranks['charlie'], 1)
        self.assertEqual(ranks['delta'], 4, 'fourth place, not second')

    def test_the_rank_badge_renders_the_shared_place(self):
        html = self.client.get('/home/').content.decode()
        import re
        badges = re.findall(r'class="n muted-3" style="font-size:12px;">(\d+)</td>', html)
        self.assertEqual(badges[:4], ['1', '1', '1', '4'])

    def test_the_podium_blocks_agree_with_the_table(self):
        html = self.client.get('/home/').content.decode()
        import re
        blocks = re.findall(r'class="pod-block">(\d+)</span>', html)
        # Rendered left-to-right as 2nd, 1st, 3rd — all tied here, so all 1.
        self.assertEqual(blocks, ['1', '1', '1'])

    def test_a_season_long_tie_shows_no_rank_change(self):
        """Positional numbering made two tied players swap places every week."""
        WeeklyLeaderboard.objects.create(league=default_league(), week=1, entries=[
            {'username': 'alpha', 'score': 5.0},
            {'username': 'bravo', 'score': 5.0},
            {'username': 'charlie', 'score': 5.0},
            {'username': 'delta', 'score': 1.0},
        ])
        board = self.client.get('/home/').context['leaderboard']
        for entry in board:
            if entry['username'] in ('alpha', 'bravo', 'charlie'):
                self.assertEqual(entry['rank_change'], 0,
                                 f"{entry['username']} was tied 1st and still is")

    def test_the_ajax_leaderboard_sends_the_rank(self):
        import json
        resp = self.client.get('/home/leaderboard/', {'week': 2})
        rows = json.loads(resp.content)
        rows = rows.get('entries', rows) if isinstance(rows, dict) else rows
        ranks = {r['username']: r['rank'] for r in rows}
        self.assertEqual(ranks['alpha'], 1)
        self.assertEqual(ranks['delta'], 4)


class HomePicksCardTests(TestCase):
    """The card on the home page answers four questions and nothing else: are
    picks out, when do they come out, when do they lock, are the preseason picks
    in. It replaced a full-width band that carried a live countdown."""

    def setUp(self):
        self.user = make_member('hp', password='pw', email='hp@x.com')
        self.client.force_login(self.user)
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 4          # past the week-1 preseason redirect
        self.settings.auto_enabled = True
        self.settings.save()

    def _html(self):
        return self.client.get('/home/').content.decode()

    def test_unpublished_shows_the_scheduled_out_time(self):
        self.settings.publish = False
        self.settings.auto_scrape_dt = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
        self.settings.save()
        html = self._html()
        self.assertIn('Not out', html)
        self.assertIn('2026-09-01', html)
        # The lock is derived from the first kickoff at publish time, so before
        # the week opens there is no honest time to show.
        self.assertIn('Set when the week opens', html)

    def test_without_autopilot_it_does_not_invent_an_out_time(self):
        self.settings.publish = False
        self.settings.auto_enabled = False
        self.settings.auto_scrape_dt = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
        self.settings.save()
        html = self._html()
        self.assertNotIn('2026-09-01', html)
        self.assertIn('Set when the week opens', html)

    def test_open_shows_the_lock_time_and_links_to_picks(self):
        self.settings.publish = True
        self.settings.lock_picks = False
        self.settings.auto_lock_dt = datetime(2026, 9, 13, 16, 40, tzinfo=timezone.utc)
        self.settings.save()
        html = self._html()
        self.assertIn('status is-open', html)
        self.assertIn('2026-09-13', html)
        self.assertIn('Make your picks', html)
        self.assertIn('/picks/', html)

    def test_locked_says_so(self):
        self.settings.publish = True
        self.settings.lock_picks = True
        self.settings.save()
        html = self._html()
        self.assertIn('status is-locked', html)
        self.assertIn('Watch my picks', html)

    def test_dates_convert_in_the_browser(self):
        """Rendered server-side but stamped for the viewer's timezone, and the
        day and time must convert together or they can disagree by a day."""
        self.settings.publish = True
        self.settings.auto_lock_dt = datetime(2026, 9, 13, 16, 40, tzinfo=timezone.utc)
        self.settings.save()
        html = self._html()
        self.assertIn('data-utc-daytime="2026-09-13', html)

    def test_the_emails_feed_is_on_the_home_page(self):
        """It briefly lived at /emails/. There is no such page now: the feed is
        a column on home, under the picks card."""
        from django.urls import NoReverseMatch, reverse
        self.assertIn('home-mail', self._html())
        with self.assertRaises(NoReverseMatch):
            reverse('main:emails')


class RankChangeTests(TestCase):
    """The arrow beside each score, on both paths that render it.

    The live poll used to look up `WeeklyLeaderboard(week=settings.week)` for its
    baseline. That row is written when a week is advanced *away* from, so during
    the week itself it does not exist: the lookup always missed and every arrow
    came back flat. The page rendered real movement and the first poll — five
    seconds later — replaced it all with dashes.
    """

    def setUp(self):
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 3
        self.settings.save()
        for name, score in (('alice', 31.0), ('bob', 21.0), ('carol', 40.0)):
            u = make_member(name, email=f'{name}@x.com')
            u.profile.score = score
            u.profile.save()
        # Standings going into week 2: carol was last, and has since leapfrogged.
        WeeklyLeaderboard.objects.create(league=default_league(), week=2, entries=[
            {'username': 'alice', 'score': 30.0},
            {'username': 'bob', 'score': 20.0},
            {'username': 'carol', 'score': 10.0}])
        self.viewer = make_member('viewer', password='pw', email='v@x.com')
        self.viewer.profile.score = 0
        self.viewer.profile.save()
        self.client.force_login(self.viewer)

    def _page(self):
        return {e['username']: e['rank_change']
                for e in self.client.get('/home/').context['leaderboard']}

    def _live(self, week=3):
        import json
        resp = self.client.get('/home/leaderboard/', {'week': week})
        return {e['username']: e['rank_change']
                for e in json.loads(resp.content)['entries']}

    def test_the_page_shows_movement(self):
        self.assertEqual(self._page()['carol'], 2, 'carol went 3rd to 1st')
        self.assertEqual(self._page()['alice'], -1)

    def test_the_live_poll_agrees_with_the_page(self):
        """Otherwise the arrows change five seconds after the page loads."""
        self.assertEqual(self._live(), self._page())

    def test_ties_do_not_register_as_movement(self):
        """Two players level all season kept swapping arbitrary order, which
        positional ranking reported as a rank change every week."""
        for name in ('alice', 'bob', 'carol'):
            u = User.objects.get(username=name)
            u.profile.score = 10.0
            u.profile.save()
        WeeklyLeaderboard.objects.filter(week=2).update(entries=[
            {'username': 'alice', 'score': 10.0},
            {'username': 'bob', 'score': 10.0},
            {'username': 'carol', 'score': 10.0}])
        self.assertEqual(set(self._page()[n] for n in ('alice', 'bob', 'carol')), {0})

    def test_during_live_grading_it_measures_this_week(self):
        """Once picks lock the baseline is the stored score — where everyone
        stood when the week began — so the arrow tracks the day's results."""
        self.settings.lock_picks = True
        self.settings.save()
        # bob is on 21 and needs to clear carol's 40 to climb two places, so the
        # underdog has to be worth 20 - at 9 he only reaches 30 and passes nobody.
        game = make_game(week=3, points1=1.0, points2=20.0, winner='team2', graded=True)
        Pick.objects.create(user=User.objects.get(username='bob'), game=game, choice='team2')

        live = self._live()
        self.assertEqual(live['bob'], 2, 'bob should climb two places on the day')
        self.assertEqual(live['carol'], -1)
        self.assertEqual(live['alice'], -1)

    def test_a_week_with_no_baseline_reports_no_movement(self):
        """Week 1, or a missing snapshot: no arrows rather than wrong ones."""
        WeeklyLeaderboard.objects.all().delete()
        self.assertEqual(set(self._page().values()), {0})


class EmailAddressExplainerTests(TestCase):
    """The Emails dashboard explains what each tagged address means.

    One Gmail mailbox does three jobs. Gmail delivers `user+tag@` to `user@` and
    keeps the tag in the headers, so the tag is what decides whether a message is
    an announcement, a set of picks, or the week's intro — and that is not
    guessable from the addresses alone.
    """

    def setUp(self):
        self.user = make_member('ed', password='pw', email='ed@x.com')
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)

    def _html(self, **over):
        from django.test import override_settings
        opts = {'SMTP_USER': 'league@gmail.com', 'IMAP_USER': 'league@gmail.com',
                'SMTP_PASSWORD': 'x', 'IMAP_PASSWORD': 'x'}
        opts.update(over)
        with override_settings(**opts):
            return self.client.get('/dashboard/emails/').content.decode()

    def test_all_three_addresses_are_listed(self):
        html = self._html()
        self.assertIn('league@gmail.com', html)
        self.assertIn('league+picks@gmail.com', html)
        self.assertIn('league+intro@gmail.com', html)

    def test_each_address_says_what_it_does(self):
        html = self._html()
        self.assertIn('A message to the league', html)
        self.assertIn('These are my picks', html)
        self.assertIn('the intro for this week', html)

    def _with_smtp(self, ready):
        """Drive the banner directly.

        `smtp_ready()` is always False under the test runner - it calls
        `outbound_suppressed()`, which trips on the TESTING flag that stops the
        suite mailing real addresses. So the branch cannot be reached by setting
        SMTP_USER; patch the function the template's flag comes from.
        """
        from main import email_utils
        original = email_utils.smtp_ready
        email_utils.smtp_ready = lambda: ready
        self.addCleanup(setattr, email_utils, 'smtp_ready', original)
        return self.client.get('/dashboard/emails/').content.decode()

    def test_it_warns_when_the_mailbox_is_not_configured(self):
        """The old Delivery box carried this; losing it silently would hide the
        one condition under which nothing works at all."""
        self.assertIn('not configured', self._with_smtp(False))

    def test_no_warning_once_it_is_configured(self):
        self.assertNotIn('not configured', self._with_smtp(True))

    def test_the_delivery_box_is_gone(self):
        self.assertNotIn('Sending as', self._html())


class PreseasonRedirectTests(TestCase):
    """Week 1 nudges you to the preseason form — but must not trap you there.

    The gate tested `settings.week == 1` alone, with no check that the form was
    still open. Anyone who missed the deadline got bounced to a page that would
    no longer accept anything, on every single visit, with no way to reach the
    home page until the week rolled over. The "I'll do this later" escape is a
    session flag, so a new browser session closed the trap again.
    """

    def setUp(self):
        self.user = make_member('late', password='pw', email='l@x.com')
        self.user.profile.preseason_submitted = False
        self.user.profile.save()
        self.client.force_login(self.user)
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 1
        self.settings.save()

    def _home(self):
        return self.client.get('/home/')

    def test_it_nudges_while_the_form_is_open(self):
        self.settings.lock_picks = False
        self.settings.save()
        self.assertRedirects(self._home(), '/preseason/')

    def test_a_missed_deadline_does_not_trap_you(self):
        """Once week 1's picks lock, preseason is closed. Sending someone to a
        form they cannot submit, forever, is just a locked door."""
        self.settings.lock_picks = True
        self.settings.save()
        self.assertEqual(self._home().status_code, 200)

    def test_it_stays_escapable_in_a_fresh_session(self):
        self.settings.lock_picks = True
        self.settings.save()
        self.client.cookies.clear()
        self.client.force_login(self.user)
        self.assertEqual(self._home().status_code, 200)

    def test_later_lets_you_through_while_it_is_open(self):
        self.settings.lock_picks = False
        self.settings.save()
        self.client.post('/preseason/', {'defer': '1'})
        self.assertEqual(self._home().status_code, 200)

    def test_submitting_stops_the_nudge(self):
        self.settings.lock_picks = False
        self.settings.save()
        self.user.profile.preseason_submitted = True
        self.user.profile.save()
        self.assertEqual(self._home().status_code, 200)

    def test_later_weeks_never_nudge(self):
        self.settings.week = 2
        self.settings.save()
        self.assertEqual(self._home().status_code, 200)


class DashboardDoesNotTickOnLoadTests(TestCase):
    """Opening the pick dashboard must not run the autopilot.

    `pickdash` called `auto_tick(default_league())` on every GET whenever auto_enabled was set,
    so viewing the page scraped the week, published it and mailed the league — a
    page load with irreversible outward side effects. It fired from throwaway
    database copies too: the SMTP credentials are the same whichever database is
    attached, so isolating the data did not isolate the mailbox, and real members
    received real email from what was meant to be a preview.
    """

    def setUp(self):
        self.user = make_member('dash', password='pw', email='d@x.com')
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.auto_enabled = True
        self.settings.save()

        from . import views
        self.ticks = []
        import main.auto as auto_mod
        self.addCleanup(setattr, auto_mod, 'auto_tick', auto_mod.auto_tick)
        auto_mod.auto_tick = lambda *a, **k: self.ticks.append(1)

    def test_a_page_load_does_not_tick(self):
        self.client.get('/dashboard/picks/')
        self.assertEqual(self.ticks, [], 'viewing the dashboard must not publish or mail')

    def test_an_unrelated_post_does_not_tick(self):
        """Saving a setting is not a request to run the autopilot."""
        self.client.post('/dashboard/picks/', {'save_auto': '1', 'tz': 'UTC',
                                               'auto_scrape_weekday': '1',
                                               'auto_scrape_time': '09:00',
                                               'lock_mode': 'offset',
                                               'auto_lock_offset_minutes': '10',
                                               'tick_interval': '300',
                                               'season_last_week': '22',
                                               'auto_retry_window_minutes': '360'})
        self.assertEqual(self.ticks, [])

    def test_the_button_ticks(self):
        self.client.post('/dashboard/picks/', {'run_tick': '1'})
        self.assertEqual(len(self.ticks), 1)

    def test_the_button_does_nothing_with_autopilot_off(self):
        self.settings.auto_enabled = False
        self.settings.save()
        self.client.post('/dashboard/picks/', {'run_tick': '1'})
        self.assertEqual(self.ticks, [])


class RecapWithoutPicksTests(TestCase):
    """A week nobody picked still has a story.

    `build_recap` used to recompute per-player scores itself — a second pass over
    every pick, building a `game_lines` list nothing read — and then bail with
    `if not ranked: return None`. `ranked` came from that private loop, so a week
    with no picks produced no recap at all, even though `recap_stats` had results,
    upsets and the state of the title race to report. It now takes its facts from
    `recap_stats`, the same place the prompt does.
    """

    def setUp(self):
        from . import auto
        self.auto = auto
        LeagueSettings.for_league(default_league())
        for i in range(3):
            make_member(f'p{i}', email=f'p{i}@x.com')
        for i in range(4):
            make_game(week=5, team1='Chicago Bears', team2='Green Bay Packers',
                      winner='team1', graded=True)
            Game.objects.filter(week=5).update(team1='Chicago Bears')
        # No Gemini in tests: exercise the fallback, which is the path that
        # depends on `ranked` being populated.
        self.addCleanup(setattr, django_settings_module(), 'GEMINI_API_KEY',
                        getattr(django_settings_module(), 'GEMINI_API_KEY', ''))
        django_settings_module().GEMINI_API_KEY = ''

    def test_a_week_with_no_picks_still_produces_a_recap(self):
        recap = self.auto.build_recap(default_league(), 5)
        self.assertIsNotNone(recap, 'no picks is not the same as nothing to say')
        self.assertIn('Week 5', recap)

    def test_the_data_block_and_the_recap_agree_on_who_played(self):
        block, ranked = self.auto.recap_data_block(default_league(), 5)
        self.assertIsNotNone(block)
        self.assertEqual(len(ranked), User.objects.count())

    def test_a_week_with_no_graded_games_has_no_recap(self):
        self.assertIsNone(self.auto.build_recap(default_league(), 9))


def django_settings_module():
    from django.conf import settings as s
    return s


class Week1HasNoRecapTests(TestCase):
    """Week 1 has no previous week, so its email never carries a recap.

    `weekly_recap` is one field that survives a season boundary. Without a guard,
    the opening email of a new season could lead with last season's closing
    write-up under a "Last Week" heading — the text is still sitting there unless
    something cleared it.
    """

    def setUp(self):
        from . import email_utils
        self.eu = email_utils
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.publish = True
        self.settings.weekly_recap = 'LAST SEASON FINALE'
        self.settings.email_recap = True
        self.settings.save()
        make_member('reader', email='r@x.com')
        make_game(week=1)
        make_game(week=2)

        self.addCleanup(setattr, email_utils, 'send_via_mailbox',
                        email_utils.send_via_mailbox)
        self.addCleanup(setattr, email_utils, 'smtp_ready', email_utils.smtp_ready)
        self.addCleanup(setattr, email_utils, 'outbound_suppressed',
                        email_utils.outbound_suppressed)
        email_utils.smtp_ready = lambda: True
        email_utils.outbound_suppressed = lambda: False
        email_utils.send_via_mailbox = lambda *a, **k: (True, 'stubbed')

    def _body(self, week):
        self.settings.week = week
        self.settings.save()
        self.eu.send_picks_published_email(self.settings)
        return LeagueEmail.objects.order_by('-sent_at').first().body

    def test_week_one_carries_no_recap(self):
        body = self._body(1)
        self.assertNotIn('LAST SEASON FINALE', body)
        self.assertNotIn('Last Week', body)

    def test_later_weeks_still_carry_it(self):
        body = self._body(2)
        self.assertIn('LAST SEASON FINALE', body)
        self.assertIn('Last Week', body)


class ManualAdvanceDoesNotDoubleMailTests(TestCase):
    """Advancing by hand records the recap; it does not mail it separately.

    The autopilot was changed to record-only — the recap reaches the league at
    the top of next week's picks-are-live email — but the dashboard's own
    "next week" button kept mailing a standalone recap, so advancing by hand
    mailed the league twice for the same week.
    """

    def setUp(self):
        self.user = make_member('adv', password='pw', email='a@x.com')
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 3
        self.settings.save()
        make_game(week=3, winner='team1', graded=True)

        from . import email_utils, auto
        self.delivered = []
        self.addCleanup(setattr, email_utils, 'send_via_mailbox',
                        email_utils.send_via_mailbox)
        email_utils.send_via_mailbox = lambda *a, **k: (
            self.delivered.append(a), (True, 'stubbed'))[1]
        self.addCleanup(setattr, auto, 'build_recap', auto.build_recap)
        auto.build_recap = lambda league, week: f'RECAP FOR WEEK {week}'

    def test_no_standalone_recap_email(self):
        from main.models import LeagueEmail
        self.client.post('/dashboard/picks/', {'nextweek': '1'})
        self.assertEqual(self.delivered, [],
                         "the recap goes out inside next week's email, not on its own")
        self.assertEqual(LeagueEmail.objects.filter(subject='Week 3 recap').count(), 1,
                         'recorded to the feed exactly once')

    def test_the_recap_is_still_recorded(self):
        self.client.post('/dashboard/picks/', {'nextweek': '1'})
        self.settings.refresh_from_db()
        self.assertIn('RECAP FOR WEEK 3', self.settings.weekly_recap)
        self.assertTrue(WeeklyLeaderboard.objects.filter(week=3).exists())


class NoTransportSendsNothingTests(TestCase):
    """With no transport configured, nothing leaves the building.

    This is the safety net relied on while the league's real accounts sit in
    production before anyone is meant to hear from the site: `RESEND_API_KEY` is
    removed and no SMTP is configured, so every send path must return before it
    does anything. Sixteen of the nineteen imported members are not the
    commissioner, so a stray publish is a stray publish to strangers.
    """

    def setUp(self):
        from . import email_utils
        self.eu = email_utils
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.publish = True
        self.settings.week = 2
        self.settings.weekly_recap = 'RECAP'
        self.settings.save()
        for i in range(3):
            make_member(f'm{i}', email=f'm{i}@example.com')
        make_game(week=2)

        # No Resend key, no SMTP - exactly production's shape right now.
        self.addCleanup(setattr, email_utils, 'outbound_suppressed',
                        email_utils.outbound_suppressed)
        email_utils.outbound_suppressed = lambda: False

        self.delivered = []
        self.addCleanup(setattr, email_utils, 'send_via_mailbox',
                        email_utils.send_via_mailbox)
        email_utils.send_via_mailbox = lambda *a, **k: (
            self.delivered.append(a), (True, 'stubbed'))[1]

    def _no_transport(self):
        from django.test import override_settings
        return override_settings(RESEND_API_KEY='', SMTP_USER='', SMTP_PASSWORD='',
                                 IMAP_USER='', IMAP_PASSWORD='')

    def test_picks_live_sends_nothing(self):
        with self._no_transport():
            self.eu.send_picks_published_email(self.settings)
        self.assertEqual(self.delivered, [])

    def test_the_reminder_sends_nothing(self):
        with self._no_transport():
            self.assertEqual(self.eu.send_pick_reminder_email(self.settings), 0)
        self.assertEqual(self.delivered, [])

    def test_smtp_ready_is_false_without_credentials(self):
        with self._no_transport():
            self.assertFalse(self.eu.smtp_ready())


class ManualScrapeHonoursGameDaysTests(TestCase):
    """The dashboard's Scrape button obeys `scrape_days`, like the autopilot.

    It had no day filter at all, so it pulled every game in the week whatever
    the league had configured. That matters beyond a stray fixture: the lock time
    is derived from the earliest kickoff *actually stored*, so one Thursday
    nighter nobody picks dragged the deadline two days earlier than the first
    game anyone could pick.
    """

    def setUp(self):
        self.user = make_member('sc2', password='pw', email='s@x.com')
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 1
        self.settings.auto_tz = 'America/New_York'
        self.settings.scrape_days = '4,5,6'          # Friday, Saturday, Sunday
        self.settings.lock_mode = 'offset'
        self.settings.auto_lock_offset_minutes = 20
        self.settings.save()

        # A real week's shape: Thursday opener, Sunday slate, Monday nighter.
        self.thu = datetime(2026, 9, 10, 0, 20, tzinfo=timezone.utc)   # Wed 20:20 ET
        self.fri = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)
        self.sun = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)
        self.mon = datetime(2026, 9, 15, 0, 15, tzinfo=timezone.utc)
        self.addCleanup(setattr, scrape, 'scrape', scrape.scrape)
        scrape.scrape = lambda **kw: [
            ('Dallas Cowboys', 'Philadelphia Eagles', -150, 130, True, 'g_thu', self.thu),
            ('Kansas City Chiefs', 'Los Angeles Chargers', -160, 140, True, 'g_fri', self.fri),
            ('Chicago Bears', 'Green Bay Packers', -120, 105, True, 'g_sun', self.sun),
            ('Minnesota Vikings', 'Detroit Lions', -130, 115, True, 'g_mon', self.mon),
        ]

    def _scrape(self):
        return self.client.post('/dashboard/picks/', {
            'scrape': '1', 'scrape_week': '1',
            'scrape_api': 'nfl_data_py', 'grade_api': 'espn', 'scrape_year': '2026'})

    def test_only_the_configured_days_are_stored(self):
        self._scrape()
        stored = {g.game_id for g in Game.objects.filter(week=1)}
        self.assertEqual(stored, {'g_fri', 'g_sun'},
                         'Thursday and Monday are not days this league plays')

    def test_the_lock_follows_the_first_game_that_counts(self):
        """Not the Thursday nighter, which never enters the slate."""
        self._scrape()
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.first_game_dt, self.fri)
        self.assertEqual(self.settings.auto_lock_dt,
                         self.fri - timedelta(minutes=20))
        self.assertGreater(self.settings.auto_lock_dt, self.thu,
                           'the lock must not precede a game nobody can pick')

    def test_no_filter_still_takes_everything(self):
        self.settings.scrape_days = ''
        self.settings.save()
        self._scrape()
        self.assertEqual(Game.objects.filter(week=1).count(), 4)


class LeagueRecipientsAreDedupedTests(TestCase):
    """One address per person, not one per account.

    Three accounts share agvdog@gmail.com, so that inbox received three copies
    of every email the league sent.
    """

    def setUp(self):
        from . import email_utils
        self.eu = email_utils
        for name in ('one', 'two', 'three'):
            make_member(name, email='shared@example.com')
        make_member('other', email='other@example.com')
        bot = make_member('abot', email='bot@example.com')
        bot.profile.is_bot = True
        bot.profile.save()

    def test_a_shared_address_appears_once(self):
        r = self.eu.league_recipients(default_league())
        self.assertEqual(r.count('shared@example.com'), 1)
        self.assertEqual(len(r), 2)

    def test_case_does_not_defeat_it(self):
        u = User.objects.get(username='other')
        u.email = 'SHARED@example.com'
        u.save()
        self.assertEqual(len(self.eu.league_recipients(default_league())), 1)

    def test_bots_are_still_excluded(self):
        self.assertNotIn('bot@example.com', self.eu.league_recipients(default_league()))


class CopyLeagueAddressesTests(TestCase):
    """The Emails dashboard offers the league's addresses for pasting into BCC.

    There is no mailing list by design - one mailbox does everything - so mail
    the site is not sending needs the addresses to reach the commissioner's own
    client somehow.
    """

    def setUp(self):
        self.user = make_member('boss4', password='pw', email='b@x.com')
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        make_member('mate1', email='mate1@example.com')
        make_member('mate2', email='mate2@example.com')

    def _html(self):
        return self.client.get('/dashboard/emails/').content.decode()

    def test_the_league_address_is_what_it_offers_first(self):
        """The mailbox *is* the league address - the site fans it out."""
        html = self._html()
        self.assertIn('Copy league address', html)
        self.assertLess(html.index('Copy league address'), html.index('member address'))

    def test_the_member_list_is_the_secondary_option(self):
        html = self._html()
        self.assertIn('member address', html)
        self.assertIn('mate1@example.com', html)
        self.assertIn('BCC', html)

    def test_it_warns_against_copying_both(self):
        """BCC is stripped in transit, so the relay cannot tell who was already
        reached and would forward to everyone a second time."""
        self.assertIn('second time', self._html())

    def test_it_warns_when_the_viewer_cannot_publish_by_email(self):
        """Without the flag, mail to the league address is read as picks."""
        self.assertFalse(self.user.profile.email_posts_enabled)
        self.assertIn('not set to publish by email', self._html())

    def test_no_warning_once_the_viewer_can_publish(self):
        self.user.profile.email_posts_enabled = True
        self.user.profile.save()
        self.assertNotIn('not set to publish by email', self._html())

    def test_the_list_is_comma_separated(self):
        resp = self.client.get('/dashboard/emails/')
        self.assertIn(', ', resp.context['recipient_list'])
        self.assertEqual(len(resp.context['recipient_list'].split(', ')),
                         len(resp.context['recipients']))

    def test_a_shared_address_is_offered_once(self):
        make_member('dupe', email='mate1@example.com')
        resp = self.client.get('/dashboard/emails/')
        self.assertEqual(resp.context['recipient_list'].count('mate1@example.com'), 1)

    def test_members_cannot_see_it(self):
        self.client.logout()
        plain = make_member('plain', password='pw', email='p@x.com')
        self.client.force_login(plain)
        resp = self.client.get('/dashboard/emails/')
        self.assertIn(resp.status_code, (302, 403),
                      "the roster's addresses are staff-only")


class ManualLockModeSurvivesAdvanceTests(TestCase):
    """A manual lock is a weekly clock. do_advance_week cleared auto_lock_dt and
    only then checked it, so the "+7 days" never ran: from the second week on
    there was no lock time and the season silently stalled."""

    def setUp(self):
        from . import auto
        self.auto = auto
        self.addCleanup(setattr, auto, 'build_recap', auto.build_recap)
        auto.build_recap = lambda league, week: None
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 3
        self.settings.lock_mode = 'manual'
        self.settings.save()
        make_game(week=3, graded=True, winner='team1')

    def _advance_with_lock(self, lock):
        self.settings.auto_lock_dt = lock
        self.settings.save()
        self.auto.do_advance_week(self.settings)
        self.settings.refresh_from_db()
        return self.settings.auto_lock_dt

    def test_the_lock_rolls_forward_one_week(self):
        lock = datetime.now(timezone.utc) + timedelta(days=1)
        self.assertEqual(self._advance_with_lock(lock), lock + timedelta(days=7))

    def test_a_stale_lock_rolls_forward_until_it_is_ahead(self):
        lock = datetime.now(timezone.utc) - timedelta(days=10)
        self.assertEqual(self._advance_with_lock(lock), lock + timedelta(days=14))

    def test_offset_mode_still_clears_the_lock(self):
        self.settings.lock_mode = 'offset'
        lock = datetime.now(timezone.utc) + timedelta(days=1)
        self.assertIsNone(self._advance_with_lock(lock),
                          'offset mode derives the lock from the next slate')


class NewSeasonTests(TestCase):
    """Save season & reset must archive before it wipes, and must not wipe
    without archiving. It used to do the opposite: the form never validated
    (the page posted no year) and the reset ran regardless."""

    def setUp(self):
        self.admin = make_member('boss', password='pw', email='b@x.com')
        self.admin.is_staff = self.admin.is_superuser = True
        self.admin.save()
        self.client.force_login(self.admin)
        self.alice = make_member('alice')
        self.alice.profile.score = 40
        self.alice.profile.preseason_submitted = True
        self.alice.profile.save()
        self.bob = make_member('bob')
        self.bob.profile.score = 25.5
        self.bob.profile.save()
        settings = LeagueSettings.for_league(default_league())
        settings.week = 5
        settings.publish = True
        settings.save()
        game = make_game(week=4, graded=True, winner='team2')
        Pick.objects.create(user=self.alice, game=game, choice='team2')
        Pick.objects.create(user=self.bob, game=game, choice='team1')
        WeeklyLeaderboard.objects.create(league=default_league(), week=4, entries=[
            {'username': 'alice', 'score': 37.5}, {'username': 'bob', 'score': 25.5}])

    def test_without_a_year_nothing_is_reset(self):
        from .models import SeasonRecord
        self.client.post('/dashboard/picks/', {'newseason': '1'})
        self.assertEqual(SeasonRecord.objects.count(), 0)
        self.alice.profile.refresh_from_db()
        self.assertEqual(self.alice.profile.score, 40)
        self.assertEqual(Game.objects.count(), 1)
        self.assertEqual(WeeklyLeaderboard.objects.count(), 1)
        self.assertEqual(LeagueSettings.for_league(default_league()).week, 5)

    def test_with_a_year_the_season_is_archived_then_reset(self):
        from .models import SeasonRecord
        self.client.post('/dashboard/picks/',
                         {'newseason': '1', 'year': '2025', 'notes': 'a good one'})
        rec = SeasonRecord.objects.get(year=2025)
        self.assertEqual(rec.winner_username, 'alice')
        self.assertEqual(rec.notes, 'a good one')
        self.assertEqual([e['username'] for e in rec.final_standings][:2], ['alice', 'bob'])
        alice, bob = rec.final_standings[0], rec.final_standings[1]
        self.assertEqual((alice['rank'], alice['correct'], alice['graded']), (1, 1, 1))
        self.assertIsNotNone(alice['preseason'])
        self.assertEqual((bob['rank'], bob['correct'], bob['graded']), (2, 0, 1))
        self.assertIsNone(bob['preseason'], 'never submitted, so no picks to show')
        self.assertEqual(rec.weeks, 1)
        self.assertEqual([w['week'] for w in rec.weekly], [4, 5])
        self.assertEqual(rec.weekly[-1]['entries'][0], {'username': 'alice', 'score': 40.0})

        self.alice.profile.refresh_from_db()
        self.assertEqual(self.alice.profile.score, 0)
        self.assertFalse(self.alice.profile.preseason_submitted)
        self.assertEqual(Game.objects.count(), 0)
        self.assertEqual(Pick.objects.count(), 0)
        self.assertEqual(WeeklyLeaderboard.objects.count(), 0)
        settings = LeagueSettings.for_league(default_league())
        self.assertEqual((settings.week, settings.publish), (1, False))

    def test_finishes_read_newest_first_and_cope_with_old_records(self):
        from .models import SeasonRecord
        from .seasons import finishes_by_username
        SeasonRecord.objects.create(league=default_league(), year=2024, winner_username='bob', final_standings=[
            {'username': 'bob', 'score': 50}, {'username': 'alice', 'score': 30}])
        self.client.post('/dashboard/picks/', {'newseason': '1', 'year': '2025'})
        fin = finishes_by_username(default_league())
        self.assertEqual([f['year'] for f in fin['alice']], [2025, 2024])
        self.assertEqual(fin['alice'][0]['rank'], 1)
        self.assertEqual(fin['alice'][1]['rank'], 2, 'recomputed for an old-shape record')
        self.assertEqual(fin['alice'][1]['players'], 2)


class DashboardNextWeekTests(TestCase):
    """The dashboard's Next week button is the autopilot's advance, not a copy
    of it. The copy forgot to clear the intro, the grade time and the retry
    state, so last week's note went out again with this week's games."""

    def setUp(self):
        admin = make_member('boss', password='pw', email='b@x.com')
        admin.is_staff = admin.is_superuser = True
        admin.save()
        self.client.force_login(admin)
        now = datetime.now(timezone.utc)
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.week = 3
        self.settings.publish = True
        self.settings.lock_picks = True
        self.settings.weekly_intro = 'OLD INTRO'
        self.settings.auto_grade_dt = now - timedelta(hours=1)
        self.settings.auto_first_attempt_dt = now - timedelta(hours=2)
        self.settings.auto_last_issue = 'sources disagreed'
        self.settings.reminder_sent_week = 3
        self.settings.save()
        make_game(week=3, graded=True, winner='team1')
        from . import auto
        self.addCleanup(setattr, auto, 'build_recap', auto.build_recap)
        auto.build_recap = lambda league, week: None

    def test_advancing_by_hand_clears_the_weekly_state(self):
        self.client.post('/dashboard/picks/', {'nextweek': '1'})
        s = LeagueSettings.for_league(default_league())
        self.assertEqual(s.week, 4)
        self.assertEqual(s.weekly_intro, '')
        self.assertIsNone(s.auto_grade_dt)
        self.assertIsNone(s.auto_first_attempt_dt)
        self.assertEqual(s.auto_last_issue, '')
        self.assertEqual(s.reminder_sent_week, 0)
        self.assertFalse(s.publish)
        self.assertFalse(s.lock_picks)
        self.assertTrue(WeeklyLeaderboard.objects.filter(week=3).exists())


class UnpublishStopsAutopilotTests(TestCase):
    """Taking a week down must not have the next tick put it straight back up.
    auto_scrape_dt was left in the past, so the worker re-scraped, re-published
    and mailed the league again within five minutes."""

    def setUp(self):
        admin = make_member('boss', password='pw', email='b@x.com')
        admin.is_staff = admin.is_superuser = True
        admin.save()
        self.client.force_login(admin)
        self.settings = LeagueSettings.for_league(default_league())
        self.settings.auto_enabled = True
        self.settings.week = 3
        self.settings.publish = True
        self.settings.auto_scrape_dt = datetime.now(timezone.utc) - timedelta(hours=1)
        self.settings.auto_last_issue = 'stale'
        self.settings.save()
        from . import auto
        self.auto = auto
        self.scrapes = []
        self.addCleanup(setattr, auto, 'do_scrape_and_publish', auto.do_scrape_and_publish)
        auto.do_scrape_and_publish = lambda s, **kw: self.scrapes.append(1)

    def test_unpublishing_clears_the_scrape_time(self):
        self.client.post('/dashboard/picks/', {'toggle_publish': '1'})
        s = LeagueSettings.for_league(default_league())
        self.assertFalse(s.publish)
        self.assertIsNone(s.auto_scrape_dt)
        self.assertEqual(s.auto_last_issue, '')

    def test_the_next_tick_does_not_republish(self):
        self.client.post('/dashboard/picks/', {'toggle_publish': '1'})
        self.auto.auto_tick(default_league())
        self.assertEqual(self.scrapes, [])
        self.assertFalse(LeagueSettings.for_league(default_league()).publish)


# ─────────────────────────────────────────────────────────────────────────────
# Leagues
# ─────────────────────────────────────────────────────────────────────────────


class LeagueIsolationTests(TestCase):
    """A member sees their league and nothing else - games, standings, members,
    history, mail - and cannot act on another league's rows by id."""

    def setUp(self):
        self.a = default_league()
        self.b = make_league('other', 'Other League')
        self.alice = make_member('alice', password='pw', league=self.a)
        self.bob = make_member('bob', password='pw', league=self.b)
        for u in (self.alice, self.bob):
            u.profile.preseason_submitted = True
            u.profile.save()
        for league in (self.a, self.b):
            s = LeagueSettings.for_league(league)
            s.week = 2
            s.publish = True
            s.save()
        self.ga = make_game(week=2, league=self.a)
        self.gb = make_game(week=2, league=self.b,
                            team1='Buffalo Bills', team2='Miami Dolphins')
        self.client.login(username='alice', password='pw')

    def test_the_picks_page_shows_only_my_leagues_games(self):
        games = self.client.get('/picks/').context['games']
        self.assertEqual([g.id for g in games], [self.ga.id])

    def test_a_pick_on_another_leagues_game_is_refused(self):
        resp = self.client.post('/picks/save/', {'game_id': self.gb.id, 'choice': 'team1'})
        self.assertFalse(resp.json()['ok'])
        self.assertEqual(Pick.objects.count(), 0)

    def test_standings_and_members_list_only_my_league(self):
        home = self.client.get('/home/')
        self.assertEqual([e['username'] for e in home.context['leaderboard']], ['alice'])
        members = self.client.get('/members/')
        self.assertEqual([m['username'] for m in members.context['members']], ['alice'])

    def test_history_data_is_scoped(self):
        data = self.client.get('/history/data/?week=2').json()
        self.assertEqual([g['id'] for g in data['games']], [self.ga.id])
        self.assertEqual([p['username'] for p in data['players']], ['alice'])

    def test_the_mail_feed_is_scoped(self):
        now = datetime.now(timezone.utc)
        LeagueEmail.objects.create(league=self.b, subject='B only', body='x',
                                   sent_at=now, message_id='<b1@x>')
        LeagueEmail.objects.create(league=self.a, subject='A only', body='x',
                                   sent_at=now, message_id='<a1@x>')
        feed = self.client.get('/home/').context['emails']
        self.assertEqual([e.subject for e in feed], ['A only'])

    def test_a_manager_cannot_delete_another_leagues_game(self):
        self.alice.profile.role = 'manager'
        self.alice.profile.save()
        resp = self.client.post('/dashboard/picks/delete-game/', {'game_id': self.gb.id})
        self.assertFalse(resp.json()['ok'])
        self.assertTrue(Game.objects.filter(pk=self.gb.pk).exists())

    def test_bot_picks_stay_in_their_league(self):
        bot = make_member('bot_a', league=self.a)
        bot.profile.is_bot = True
        bot.profile.save()
        make_bot_picks(self.b)
        self.assertEqual(Pick.objects.filter(user=bot).count(), 0)
        make_bot_picks(self.a)
        self.assertEqual(Pick.objects.filter(user=bot, game=self.ga).count(), 1)


class WorkerTicksEveryLeagueTests(TestCase):
    """The worker ticks every active league once per pass, and one league
    raising must not stop the others."""

    def setUp(self):
        from . import auto, inbound_email
        self.auto = auto
        self.addCleanup(setattr, inbound_email, 'fetch', inbound_email.fetch)
        inbound_email.fetch = lambda *a, **k: (0, 0)
        self.ticked = []
        self.addCleanup(setattr, auto, 'auto_tick', auto.auto_tick)

        def fake_tick(league):
            self.ticked.append(league.slug)
            if league.slug == 'broken':
                raise RuntimeError('boom')
        auto.auto_tick = fake_tick

    def test_every_active_league_is_ticked(self):
        make_league('broken')
        make_league('quiet')
        closed = make_league('closed')
        closed.is_active = False
        closed.save()
        interval = self.auto.tick_all_leagues()
        self.assertEqual(sorted(self.ticked), ['broken', 'putnambowl', 'quiet'])
        self.assertEqual(interval, 300)

    def test_the_interval_is_the_shortest_leagues(self):
        fast = make_league('fast')
        s = LeagueSettings.for_league(fast)
        s.tick_interval = 60
        s.save()
        s = LeagueSettings.for_league(default_league())
        s.tick_interval = 120
        s.save()
        self.assertEqual(self.auto.tick_all_leagues(), 60)


class RecipientsScopedTests(TestCase):
    """League mail goes to that league's members and nobody else's."""

    def setUp(self):
        from . import email_utils
        self.eu = email_utils
        self.a = default_league()
        self.b = make_league('b')
        make_member('a1', email='a1@x.com', league=self.a)
        make_member('b1', email='b1@x.com', league=self.b)

    def test_recipients_are_per_league(self):
        self.assertEqual(self.eu.league_recipients(self.a), ['a1@x.com'])
        self.assertEqual(self.eu.league_recipients(self.b), ['b1@x.com'])

    def test_missing_picks_are_per_league(self):
        make_game(week=1, league=self.a)
        make_game(week=1, league=self.b)
        self.assertEqual([u.username for u, *_ in self.eu.members_missing_picks(self.a, 1)], ['a1'])
        self.assertEqual([u.username for u, *_ in self.eu.members_missing_picks(self.b, 1)], ['b1'])

    def test_the_picks_live_mail_is_recorded_against_its_league(self):
        s = LeagueSettings.for_league(self.b)
        s.week = 1
        s.publish = True
        s.save()
        make_game(week=1, league=self.b)
        self.addCleanup(setattr, self.eu, 'outbound_suppressed', self.eu.outbound_suppressed)
        self.eu.outbound_suppressed = lambda: False
        self.addCleanup(setattr, self.eu, 'smtp_ready', self.eu.smtp_ready)
        self.eu.smtp_ready = lambda: True
        self.addCleanup(setattr, self.eu, 'send_via_mailbox', self.eu.send_via_mailbox)
        self.eu.send_via_mailbox = lambda *a, **k: (True, 'stubbed')
        self.eu.send_picks_published_email(s)
        row = LeagueEmail.objects.get(subject='Week 1 picks are live')
        self.assertEqual(row.league, self.b)
        self.assertIn('b-picks-live-w1', row.message_id)
        self.assertEqual(row.recipient_count, 1)
        self.assertEqual(LeagueEmail.objects.filter(league=self.a).count(), 0)


class EmailPrefsTests(TestCase):
    """A member can opt out of the weekly mail and the reminder without the
    manager switching either off for everyone."""

    def test_opt_outs_are_honoured(self):
        from . import email_utils
        a = default_league()
        make_member('u1', email='u1@x.com')
        u2 = make_member('u2', email='u2@x.com')
        u2.profile.email_weekly = False
        u2.profile.email_reminder = False
        u2.profile.save()
        make_game(week=1)
        self.assertEqual(email_utils.league_recipients(a), ['u1@x.com', 'u2@x.com'],
                         'relayed league correspondence still reaches everyone')
        self.assertEqual(email_utils.league_recipients(a, weekly=True), ['u1@x.com'])
        self.assertEqual([u.username for u, *_ in email_utils.members_missing_picks(a, 1)], ['u1'])


class InboundRoutingTests(TestCase):
    """One mailbox serves every league; a message is routed by its sender."""

    def _raw(self, sender, to, msgid, body='Picks are open, get them in.'):
        return '\r\n'.join([
            f'From: Boss <{sender}>',
            f'To: {to}',
            'Subject: Week 1 is live',
            f'Message-ID: {msgid}',
            'Date: Mon, 22 Sep 2025 10:00:00 +0000',
            'Authentication-Results: mx.example.com; dmarc=pass',
            'Content-Type: text/plain; charset="utf-8"',
            '', body,
        ]).encode()

    def setUp(self):
        from . import inbound_email
        self.ingest = inbound_email.ingest_message
        self.a = default_league()
        self.b = make_league('b', 'Bravo')
        for name, email, league in (('bossa', 'boss@a.com', self.a), ('bossb', 'boss@b.com', self.b)):
            u = make_member(name, email=email, league=league)
            u.profile.email_posts_enabled = True
            u.profile.save()

    def test_an_announcement_lands_in_the_senders_league(self):
        with self.settings(SMTP_USER='mailbox@gmail.com'):
            obj, reason = self.ingest(self._raw('boss@b.com', 'mailbox@gmail.com', '<m1@x>'))
        self.assertIsNotNone(obj, reason)
        self.assertEqual(obj.league, self.b)

    def test_a_sender_in_two_leagues_is_refused_and_left_for_later(self):
        from .models import ProcessedEmail
        dup = make_member('dup', email='boss@b.com', league=self.a)
        dup.profile.email_posts_enabled = True
        dup.profile.save()
        with self.settings(SMTP_USER='mailbox@gmail.com'):
            obj, reason = self.ingest(self._raw('boss@b.com', 'mailbox@gmail.com', '<m2@x>'))
        self.assertIsNone(obj)
        self.assertIn('more than one league', reason)
        self.assertEqual(ProcessedEmail.objects.count(), 0, 'fixing the accounts must re-ingest it')

    def test_an_intro_by_email_sets_the_senders_league_intro(self):
        with self.settings(SMTP_USER='mailbox@gmail.com'):
            self.ingest(self._raw('boss@b.com', 'mailbox+intro@gmail.com', '<m3@x>',
                                  body='Week {week} is here.'))
        self.assertEqual(LeagueSettings.for_league(self.b).weekly_intro, 'Week {week} is here.')
        self.assertEqual(LeagueSettings.for_league(self.a).weekly_intro, '')


class JoinCodeTests(TestCase):
    """An account cannot exist outside a league, so registration needs a code."""

    def _post(self, **extra):
        data = {'username': 'newbie', 'email': 'n@x.com',
                'password1': 'Str0ngpass!', 'password2': 'Str0ngpass!'}
        data.update(extra)
        return self.client.post('/register/', data)

    def test_no_code_no_account(self):
        self._post()
        self.assertFalse(User.objects.filter(username='newbie').exists())

    def test_a_wrong_code_is_refused(self):
        resp = self._post(join_code='NOPE1234')
        self.assertFalse(User.objects.filter(username='newbie').exists())
        self.assertContains(resp, 'does not match')

    def test_a_valid_code_joins_that_league(self):
        b = make_league('b')
        self._post(join_code=b.join_code.lower())
        user = User.objects.get(username='newbie')
        self.assertEqual(user.profile.league, b)
        self.assertEqual(user.profile.role, 'member')

    def test_the_join_link_prefills_the_code_and_names_the_league(self):
        b = make_league('b', 'Bravo League')
        resp = self.client.get(f'/join/{b.join_code}/')
        self.assertContains(resp, b.join_code)
        self.assertContains(resp, 'Bravo League')

    def test_a_closed_league_cannot_be_joined(self):
        b = make_league('b')
        b.is_active = False
        b.save()
        self._post(join_code=b.join_code)
        self.assertFalse(User.objects.filter(username='newbie').exists())

    def test_rotating_the_code_invalidates_the_old_one(self):
        b = make_league('b')
        old = b.join_code
        new = b.rotate_join_code()
        self.assertNotEqual(old, new)
        self._post(join_code=old)
        self.assertFalse(User.objects.filter(username='newbie').exists())
        self._post(join_code=new)
        self.assertTrue(User.objects.filter(username='newbie').exists())


class LoginRoutingTests(TestCase):
    def test_a_superuser_without_a_league_goes_to_the_site_admin(self):
        User.objects.create_superuser('root', 'r@x.com', 'pw')
        resp = self.client.post('/login/', {'username': 'root', 'password': 'pw'})
        self.assertRedirects(resp, '/leagues/', fetch_redirect_response=False)

    def test_a_member_goes_home(self):
        make_member('m', password='pw')
        resp = self.client.post('/login/', {'username': 'm', 'password': 'pw'})
        self.assertRedirects(resp, '/home/', fetch_redirect_response=False)

    def test_next_is_honoured_only_when_safe(self):
        make_member('m', password='pw')
        resp = self.client.post('/login/?next=/picks/', {'username': 'm', 'password': 'pw'})
        self.assertRedirects(resp, '/picks/', fetch_redirect_response=False)
        self.client.logout()
        resp = self.client.post('/login/?next=http://evil.example/', {'username': 'm', 'password': 'pw'})
        self.assertRedirects(resp, '/home/', fetch_redirect_response=False)

    def test_the_site_admin_login_refuses_members(self):
        make_member('m', password='pw')
        resp = self.client.post('/leagues/login/', {'username': 'm', 'password': 'pw'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'not a site admin')
        self.assertFalse(resp.wsgi_request.user.is_authenticated)


class ManagerAccessTests(TestCase):
    def setUp(self):
        self.a = default_league()
        self.b = make_league('b')
        self.alice = make_member('alice', password='pw', league=self.a)
        self.mgr = make_member('mgr', password='pw', league=self.a, role='manager')
        self.bob = make_member('bob', password='pw', league=self.b)

    def test_a_member_is_refused(self):
        self.client.login(username='alice', password='pw')
        self.assertEqual(self.client.get('/dashboard/picks/').status_code, 403)
        self.assertEqual(self.client.get('/dashboard/emails/').status_code, 403)

    def test_a_manager_is_admitted_without_is_staff(self):
        self.assertFalse(self.mgr.is_staff)
        self.client.login(username='mgr', password='pw')
        self.assertEqual(self.client.get('/dashboard/picks/').status_code, 200)

    def test_a_manager_cannot_edit_another_leagues_member(self):
        self.client.login(username='mgr', password='pw')
        resp = self.client.post(f'/dashboard/accounts/edit/{self.bob.id}/', {'username': 'bob'})
        self.assertEqual(resp.status_code, 404)

    def test_a_manager_can_promote_a_member_of_their_own_league(self):
        self.client.login(username='mgr', password='pw')
        resp = self.client.post(f'/dashboard/accounts/edit/{self.alice.id}/',
                                {'username': 'alice', 'email': 'a@x.com', 'role': 'manager'})
        self.assertEqual(resp.status_code, 200)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.profile.role, 'manager')
        self.assertFalse(self.alice.is_staff, 'managing is a league role, not the Django flag')

    def test_a_superuser_in_no_league_is_sent_to_the_site_admin(self):
        User.objects.create_superuser('root', 'r@x.com', 'pw')
        self.client.login(username='root', password='pw')
        resp = self.client.get('/home/')
        self.assertRedirects(resp, '/leagues/', fetch_redirect_response=False)


class SuperadminAreaTests(TestCase):
    def setUp(self):
        from .models import IntroTemplate
        self.IntroTemplate = IntroTemplate
        self.root = User.objects.create_superuser('root', 'r@x.com', 'pw')
        self.client.force_login(self.root)

    def test_anonymous_is_sent_to_the_admin_login(self):
        from django.test import Client
        resp = Client().get('/leagues/')
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp['Location'].startswith('/leagues/login/'))

    def test_the_index_lists_leagues(self):
        self.assertContains(self.client.get('/leagues/'), 'PutnamBowl')

    def test_creating_a_league_seeds_settings_intros_and_a_manager(self):
        self.client.post('/leagues/new/', {
            'name': 'Bravo League', 'slug': '', 'is_active': 'on',
            'username': 'bmgr', 'email': 'b@x.com', 'password': 'Str0ngpass!',
        })
        b = League.objects.get(slug='bravo-league')
        self.assertTrue(LeagueSettings.objects.filter(league=b).exists())
        self.assertEqual(self.IntroTemplate.objects.filter(league=b).count(), 10)
        mgr = User.objects.get(username='bmgr')
        self.assertEqual((mgr.profile.league, mgr.profile.role), (b, 'manager'))
        self.assertTrue(mgr.check_password('Str0ngpass!'))

    def test_a_league_can_be_created_without_a_manager(self):
        self.client.post('/leagues/new/', {'name': 'Solo', 'slug': 'solo', 'is_active': 'on'})
        self.assertTrue(League.objects.filter(slug='solo').exists())

    def test_rotate_and_manage_managers(self):
        league = default_league()
        old = league.join_code
        self.client.post('/leagues/putnambowl/', {'rotate_code': '1'})
        league.refresh_from_db()
        self.assertNotEqual(league.join_code, old)

        m = make_member('m')
        self.client.post('/leagues/putnambowl/', {'add_manager': '1', 'username': 'm'})
        m.refresh_from_db()
        self.assertEqual(m.profile.role, 'manager')
        self.client.post('/leagues/putnambowl/', {'remove_manager': '1', 'username': 'm'})
        m.refresh_from_db()
        self.assertEqual(m.profile.role, 'member')

    def test_a_member_of_another_league_cannot_be_promoted_here(self):
        b = make_league('b')
        outsider = make_member('out', league=b)
        self.client.post('/leagues/putnambowl/', {'add_manager': '1', 'username': 'out'})
        outsider.refresh_from_db()
        self.assertEqual(outsider.profile.role, 'member')


class RecapSlugTests(TestCase):
    def test_the_same_week_in_two_leagues_is_two_feed_rows(self):
        from . import email_utils
        a = default_league()
        b = make_league('b')
        email_utils.record_recap_email(a, 3, 'A recap', year=2026)
        email_utils.record_recap_email(b, 3, 'B recap', year=2026)
        rows = LeagueEmail.objects.filter(subject='Week 3 recap')
        self.assertEqual(rows.count(), 2)
        self.assertEqual(len({r.message_id for r in rows}), 2)
        self.assertIn('A recap', rows.get(league=a).body)
        self.assertIn('B recap', rows.get(league=b).body)
        self.assertIn('B', rows.get(league=b).body.split('──')[-1])
