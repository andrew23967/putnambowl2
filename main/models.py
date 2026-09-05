from django.db import models
from django.contrib.auth.models import User
from .teams import TEAMS, canonical_game_id


class LeagueSettings(models.Model):
    """One row per league: the week it is on, and how its autopilot, email and
    recap behave. This was the site-wide `SiteSettings` singleton; nothing may
    reach it without a league - see `LeagueSettings.for_league`."""
    league = models.OneToOneField('leagues.League', on_delete=models.CASCADE,
                                  related_name='league_settings')
    week = models.IntegerField(default=1)
    publish = models.BooleanField(default=False)
    lock_picks = models.BooleanField(default=False)
    multiplier = models.IntegerField(default=1)
    scrape_week = models.IntegerField(default=1)
    # Two sources, chosen independently. nfl-data-py is the only one carrying
    # moneylines, so it should almost always be the scrape source; ESPN is the
    # only one with live scores, so it is the better grade source.
    scrape_api = models.CharField(max_length=20, default='nfl_data_py')
    grade_api = models.CharField(max_length=20, default='nfl_data_py')
    weekly_recap = models.TextField(blank=True, default='')
    auto_enabled = models.BooleanField(default=False)
    auto_scrape_weekday = models.IntegerField(default=1)   # 0=Mon … 6=Sun
    auto_scrape_hour = models.IntegerField(default=9)       # UTC 0-23
    auto_scrape_minute = models.IntegerField(default=0)     # UTC 0-59
    auto_lock_offset_minutes = models.IntegerField(default=10)
    lock_mode = models.CharField(max_length=10, default='offset')  # 'offset' or 'manual'
    auto_tz = models.CharField(max_length=50, default='UTC')
    first_game_dt = models.DateTimeField(null=True, blank=True)
    tick_interval = models.IntegerField(default=300)        # seconds between ticks
    auto_scrape_dt = models.DateTimeField(null=True, blank=True)  # exact UTC time to scrape+publish
    auto_lock_dt = models.DateTimeField(null=True, blank=True)    # exact UTC time to lock picks

    # ── Which days the league plays ──
    # A set, not a from/to range: a range can only express a contiguous run, so a
    # league that plays Sunday and Monday but skips Saturday had no way to say so.
    # Comma-separated weekday numbers, 0=Mon…6=Sun; blank means every day.
    scrape_days = models.CharField(
        max_length=20, blank=True, default='',
        help_text='Weekdays the league picks games on, 0=Mon…6=Sun. Blank = all days.')

    # ── Grading ──
    # Grading used to run on every tick from the moment picks locked, which meant
    # it hammered the source all through Sunday for results that could not exist.
    # It now starts at the first kickoff and polls each tick until every game is
    # in, which is what carries it across Monday night.
    #
    # Derived from the slate at publish time, never configured: no result can
    # exist before the first game starts, and a flexed kickoff moves it on its
    # own where a hand-set weekday and time would just sit there being wrong.
    auto_grade_dt = models.DateTimeField(null=True, blank=True)

    # ── Advancing ──
    # Advancing was unconditional and immediate: the moment the last game was
    # graded the week rolled over, with no way to hold it and no stop at the end
    # of the season, so it ran past the Super Bowl into empty week 23.
    auto_advance = models.BooleanField(
        default=True, help_text='Roll to the next week once every game is graded.')
    season_last_week = models.IntegerField(
        default=22, help_text='Final week of the season. Autopilot stops here.')

    # ── Scrape validation and retry ──
    # A scrape that came back empty or unpriced used to publish anyway and mail the
    # league. Now it retries for this long, then publishes and flags what is wrong.
    auto_retry_window_minutes = models.IntegerField(
        default=360,
        help_text='Keep retrying a bad scrape for this many minutes, then publish anyway.')
    auto_first_attempt_dt = models.DateTimeField(null=True, blank=True)
    auto_last_issue = models.TextField(blank=True, default='')

    # ── Email — the /dashboard/emails/ page ──
    # Each switch turns off one kind of mail. *When* they fire is decided by the
    # auto-pilot fields above; these only decide whether they go out at all.
    email_picks_live = models.BooleanField(
        default=True, help_text='Mail the league when a week is published.')
    email_ballot = models.BooleanField(
        default=True,
        help_text='Include the reply-by-email ballot in that mail, so members can '
                  'send picks by deleting the team they do not want.')
    email_recap = models.BooleanField(
        default=True,
        help_text="Include last week's recap in the picks-are-live email. The "
                  "recap no longer goes out on its own - one mail a week, not two.")
    email_reminder = models.BooleanField(
        default=True,
        help_text='Nudge anyone whose picks are still incomplete before they lock.')
    email_confirmations = models.BooleanField(
        default=True,
        help_text='Reply to emailed picks confirming what was recorded. Off leaves '
                  'members no way to catch a misread.')
    email_relay = models.BooleanField(
        default=True,
        help_text="Forward the commissioner's league emails on to every member.")

    # ── The weekly email ──
    # Written by hand, shown at the top of the picks-are-live mail, and cleared
    # when the week advances so last week's note cannot go out again attached to
    # this week's games. Blank simply omits the section.
    weekly_intro = models.TextField(
        blank=True, default='',
        help_text='Optional note from the commissioner, top of this week\'s email.')

    # How long before picks lock the reminder goes out. An offset rather than a
    # clock time, so it follows the slate: the lock is itself derived from the
    # first kickoff, and a flexed game moves both together.
    reminder_hours_before_lock = models.IntegerField(default=24)
    # The week the reminder last went out, so a tick every 5 minutes across the
    # whole window does not mail everyone dozens of times.
    reminder_sent_week = models.IntegerField(default=0)

    # Editable *instructions* for the recap prompt. The data it needs —
    # standings, results, and the output format rules — is always appended by the
    # code and cannot be edited away. Blank means "use the built-in default".
    recap_prompt = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'League settings'
        verbose_name_plural = 'League settings'

    def scrape_day_set(self):
        """Weekdays the league plays, as a set of ints. Empty set = every day.

        Tolerates junk in the field rather than raising inside the worker: a
        malformed value means "no filter", which scrapes everything, rather than
        an exception that stops the week being published at all.
        """
        days = set()
        for part in (self.scrape_days or '').split(','):
            part = part.strip()
            if part.isdigit() and 0 <= int(part) <= 6:
                days.add(int(part))
        return days

    @classmethod
    def for_league(cls, league):
        """The settings row for a league, created on first use."""
        if league is None:
            raise ValueError('LeagueSettings.for_league needs a league')
        obj, _ = cls.objects.get_or_create(league=league)
        return obj

    def __str__(self):
        return f'{self.league} settings (week {self.week})'


class Game(models.Model):
    WINNER_CHOICES = [
        ('team1', 'Team 1'),
        ('team2', 'Team 2'),
        ('tie', 'Tie'),
    ]

    team1 = models.CharField(max_length=50, choices=TEAMS)
    team2 = models.CharField(max_length=50, choices=TEAMS)
    points1 = models.FloatField(default=1.0)
    points2 = models.FloatField(default=1.0)
    winner = models.CharField(max_length=10, choices=WINNER_CHOICES, blank=True, default='')
    graded = models.BooleanField(default=False)
    # Named for what it means. It used to be `home_team`, documented as
    # "True = team2 is home" — the exact opposite of what every writer stored.
    # scrape.py and the manual-entry view both set it from "the favorite is at
    # home", i.e. team1, while do_grade and the templates read the help text and
    # believed team2. That single disagreement made auto-grading award every game
    # to the losing team.
    team1_is_home = models.BooleanField(
        default=True, help_text='True = team1 (the favorite) is the home team')
    game_id = models.CharField(max_length=50, blank=True, default='')
    game_dt = models.DateTimeField(null=True, blank=True)
    week = models.IntegerField(default=1)
    league = models.ForeignKey('leagues.League', on_delete=models.CASCADE,
                               related_name='games')

    @classmethod
    def match_existing(cls, league, week, team1, team2, game_id=''):
        """The stored row for this fixture, or None.

        Keyed on **who is playing**, never on the odds. team1/team2 are
        favorite/underdog, so when a line crosses pick'em the two swap places;
        the old check compared them in order, so a re-scrape after the favorite
        changed stored the same game a second time with the teams reversed.
        Comparing the pair unordered is what makes a re-scrape idempotent.

        Kickoff time is deliberately *not* part of the key. Flex scheduling moves
        games between slots all season, and a moved game is still the same game —
        keying on the time would duplicate it every time the NFL reshuffled the
        Sunday night slot. The week plus the two teams already identifies a
        fixture uniquely: two teams meet at most once in a week.

        game_id is tried first where both sides have one, canonicalised so the
        two sources' spellings compare equal.
        """
        in_week = cls.objects.filter(league=league, week=week)
        canon = canonical_game_id(game_id)
        if canon:
            for g in in_week:
                if canonical_game_id(g.game_id) == canon:
                    return g
        pair = {team1, team2}
        for g in in_week:
            if {g.team1, g.team2} == pair:
                return g
        return None

    @property
    def game_dt_iso(self):
        if self.game_dt:
            from datetime import timezone as _tz
            return self.game_dt.astimezone(_tz.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        return ''

    def __str__(self):
        return f'{self.team1} vs {self.team2}'

    @property
    def team1_abbrev(self):
        from .teams import TEAM_ABBREV
        return TEAM_ABBREV.get(self.team1, self.team1[:3].upper())

    @property
    def team2_abbrev(self):
        from .teams import TEAM_ABBREV
        return TEAM_ABBREV.get(self.team2, self.team2[:3].upper())


class Pick(models.Model):
    CHOICE_TEAM1 = 'team1'
    CHOICE_TEAM2 = 'team2'
    PICK_CHOICES = [
        (CHOICE_TEAM1, 'Team 1'),
        (CHOICE_TEAM2, 'Team 2'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='picks')
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='picks')
    choice = models.CharField(max_length=10, choices=PICK_CHOICES)

    class Meta:
        unique_together = ['user', 'game']

    def __str__(self):
        return f'{self.user.username} → {self.game} → {self.choice}'

    @property
    def is_correct(self):
        if not self.game.graded or not self.game.winner:
            return None
        return self.choice == self.game.winner

    @property
    def points_earned(self):
        if not self.is_correct:
            return 0
        return self.game.points1 if self.choice == 'team1' else self.game.points2

    @property
    def team_picked(self):
        return self.game.team1 if self.choice == 'team1' else self.game.team2

    @property
    def points_possible(self):
        return self.game.points1 if self.choice == 'team1' else self.game.points2



class WeeklyLeaderboard(models.Model):
    league = models.ForeignKey('leagues.League', on_delete=models.CASCADE,
                               related_name='leaderboards')
    week = models.IntegerField(default=1)
    entries = models.JSONField(default=list)
    recap = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['week']
        unique_together = [('league', 'week')]

    def __str__(self):
        return f'Week {self.week} Leaderboard'


class ProcessedEmail(models.Model):
    """Message-IDs the poller has already acted on.

    Deliberately separate from `LeagueEmail`: dedupe has to survive a feed row
    being deleted. The poller scans a rolling window rather than unread flags, so
    if dedupe read the feed, deleting a message still inside that window would get
    it re-ingested — and **re-relayed to the whole league**.

    Only messages that were acted on are recorded. A message *rejected* for
    configuration reasons — the sender not yet allowed to publish, say — is left
    out on purpose, so it is picked up on the next poll once that is fixed.
    """
    message_id = models.CharField(max_length=400, unique=True)
    seen_at = models.DateTimeField(auto_now_add=True)
    outcome = models.CharField(max_length=200, blank=True, default='')
    # Set when the message could not be handled for a reason that may pass — the
    # model returning 503, say. Dedupe ignores deferred rows, so the next poll
    # tries again instead of dropping someone's picks over a temporary outage.
    deferred = models.BooleanField(default=False)
    attempts = models.IntegerField(default=0)

    class Meta:
        ordering = ['-seen_at']

    def __str__(self):
        return self.message_id


class LeagueEmail(models.Model):
    """A message in the site's Emails feed.

    Two ways a row gets here:

    * **Ingested** — the commissioner (or any member with
      ``profile.email_posts_enabled``) mails the league and copies the site
      address. ``main/inbound_email.py`` verifies it and stores it.
    * **Recorded** — the site sent it itself, written at send time by
      ``email_utils``. No round trip through the mailbox, so our own mail shows
      up even if ingestion is misconfigured.

    Bodies are plain text and rendered escaped. Accepting HTML mail would need a
    real sanitiser, which is a separate decision.
    """
    SOURCE_INBOUND = 'inbound'
    SOURCE_SITE = 'site'
    SOURCE_CHOICES = [
        (SOURCE_INBOUND, 'Received from a member'),
        (SOURCE_SITE, 'Sent by the site'),
    ]

    league = models.ForeignKey('leagues.League', on_delete=models.CASCADE,
                               related_name='emails')
    author = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='league_emails',
        help_text='The member whose address it came from, when one matched.',
    )
    # Kept even when author is null so a rejected or orphaned sender is auditable.
    from_email = models.EmailField(blank=True, default='')
    from_name = models.CharField(max_length=120, blank=True, default='')
    subject = models.CharField(max_length=300, blank=True, default='')
    body = models.TextField(blank=True, default='')
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_INBOUND)
    # Date header for inbound, send time for our own. received_at is when we
    # stored it; the two differ when a poll runs well after delivery.
    sent_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    # RFC 5322 Message-ID. Unique so re-polling the same mailbox cannot double-post.
    message_id = models.CharField(max_length=400, unique=True)
    # How many league members were on it — both the "went to everyone" evidence
    # and what the page shows.
    recipient_count = models.IntegerField(default=0)
    published = models.BooleanField(
        default=True, help_text='Uncheck to hide from the site without deleting.'
    )

    class Meta:
        ordering = ['-sent_at']
        indexes = [models.Index(fields=['-sent_at'])]

    def __str__(self):
        who = self.author.username if self.author else (self.from_email or 'unknown')
        return f'{who}: {self.subject[:50]}'

    @property
    def display_name(self):
        if self.author:
            return self.author.profile.real_name or self.author.username
        return self.from_name or self.from_email


class SeasonRecord(models.Model):
    """A finished season, as it stood at "Save season & reset".

    The reset deletes every Game, Pick and WeeklyLeaderboard row, so this is the
    only record a past season leaves. `main/seasons.py` writes it.

    `final_standings` entries: {username, display_name, score, rank, correct,
    graded, is_bot, preseason: {big_loser, nfc, afc, superbowl} | None}. Records
    written before v3 carry only username and score; readers must cope.

    `weekly` is the WeeklyLeaderboard series - entry k is the table going *into*
    week k - closed with one more entry holding the final scores, so
    "score after week k" is always entry k+1.
    """
    league = models.ForeignKey('leagues.League', on_delete=models.CASCADE,
                               related_name='seasons')
    year = models.IntegerField()
    winner_username = models.CharField(max_length=150)
    final_standings = models.JSONField(default=list)
    notes = models.TextField(blank=True, default='')
    weeks = models.IntegerField(default=0)
    weekly = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-year']
        unique_together = [('league', 'year')]

    def __str__(self):
        return f'{self.year} Season — Winner: {self.winner_username}'


class IntroTemplate(models.Model):
    """A reusable opening line for the weekly email.

    The intro is the one part of that mail nobody can automate - it is the
    commissioner talking - but most weeks it says one of a handful of things.
    Keeping them named and editable means picking one is a click, and the odd
    week that needs something specific can still be typed straight into the box.

    `{week}` in the body is replaced with the week number when the mail is built,
    not when the template is chosen, so a template written once stays correct
    every time it is reused.
    """
    league = models.ForeignKey('leagues.League', on_delete=models.CASCADE,
                               related_name='intro_templates')
    name = models.CharField(max_length=60)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        unique_together = [('league', 'name')]

    def __str__(self):
        return self.name

    def render(self, week):
        """Body with `{week}` filled in.

        `replace`, never `format`: the text is user-edited and a stray brace -
        an emoticon, a bit of pasted JSON - must not raise inside the send path.
        """
        return ((self.body or '').replace('{week}', str(week))
                .replace('{league}', self.league.name))
