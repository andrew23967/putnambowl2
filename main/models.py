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
    grade_api = models.CharField(max_length=20, default='nfl_data_py')
    weekly_recap = models.TextField(blank=True, default='')
    auto_enabled = models.BooleanField(default=False)
    auto_scrape_weekday = models.IntegerField(default=1)   # 0=Mon … 6=Sun
    auto_scrape_hour = models.IntegerField(default=9)       # UTC 0-23
    auto_lock_offset_minutes = models.IntegerField(default=10)
    first_game_dt = models.DateTimeField(null=True, blank=True)
    tick_interval = models.IntegerField(default=300)        # seconds between ticks
    auto_scrape_dt = models.DateTimeField(null=True, blank=True)  # exact UTC time to scrape+publish
    auto_lock_dt = models.DateTimeField(null=True, blank=True)    # exact UTC time to lock picks

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
    date = models.CharField(max_length=50, blank=True, default='')

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


class History(models.Model):
    week = models.IntegerField(default=1, unique=True)
    games_data = models.JSONField(default=list)
    players_list = models.JSONField(default=list)

    class Meta:
        ordering = ['week']
        verbose_name_plural = 'Histories'

    def __str__(self):
        return f'Week {self.week}'


class WeeklyLeaderboard(models.Model):
    week = models.IntegerField(default=1, unique=True)
    entries = models.JSONField(default=list)

    class Meta:
        ordering = ['week']

    def __str__(self):
        return f'Week {self.week} Leaderboard'


class Announcement(models.Model):
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.message[:50]


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
