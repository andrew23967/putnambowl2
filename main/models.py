from django.db import models
from django.contrib.auth.models import User
from .teams import TEAMS


class SiteSettings(models.Model):
    week = models.IntegerField(default=1)
    publish = models.BooleanField(default=False)
    edit = models.BooleanField(default=True)
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
    scrape_filter_from_day = models.IntegerField(null=True, blank=True)  # 0=Mon…6=Sun, None=no filter
    scrape_filter_to_day = models.IntegerField(null=True, blank=True)

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
        default=True, help_text="Mail PutnamBot's recap when a week is scored.")
    email_confirmations = models.BooleanField(
        default=True,
        help_text='Reply to emailed picks confirming what was recorded. Off leaves '
                  'members no way to catch a misread.')
    email_relay = models.BooleanField(
        default=True,
        help_text="Forward the commissioner's league emails on to every member.")

    # Editable *instructions* for the Gemini prompts. The data each one needs —
    # standings, results, and the output format rules — is always appended by the
    # code and cannot be edited away. Blank means "use the built-in default".
    recap_prompt = models.TextField(blank=True, default='')
    intro_prompt = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'Site Settings'
        verbose_name_plural = 'Site Settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return f'Site Settings (Week {self.week})'


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
    home_team = models.BooleanField(default=True, help_text='True = team2 is home')
    game_id = models.CharField(max_length=50, blank=True, default='')
    game_dt = models.DateTimeField(null=True, blank=True)
    week = models.IntegerField(default=1)

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
    week = models.IntegerField(default=1, unique=True)
    entries = models.JSONField(default=list)
    recap = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['week']

    def __str__(self):
        return f'Week {self.week} Leaderboard'


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


class Message(models.Model):
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    recipients = models.CharField(max_length=50, default='Everyone')
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    read = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.sender} → {self.recipients}'


class Bug(models.Model):
    finder = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bugs')
    description = models.TextField()
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Bug by {self.finder} ({self.created_at.date()})'


class SeasonRecord(models.Model):
    year = models.IntegerField()
    winner_username = models.CharField(max_length=150)
    final_standings = models.JSONField(default=list)  # [{'username': ..., 'score': ...}, ...]
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-year']

    def __str__(self):
        return f'{self.year} Season — Winner: {self.winner_username}'
