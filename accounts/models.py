from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from main.teams import TEAMS


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    # Every account is in exactly one league. Null only for a superuser created
    # before any league existed - createsuperuser fires the profile signal too.
    league = models.ForeignKey('leagues.League', on_delete=models.PROTECT,
                               null=True, blank=True, related_name='profiles')
    ROLE_CHOICES = [('member', 'Member'), ('manager', 'Manager')]
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='member')
    # Per-member opt-outs. The switches on the Emails page decide whether a kind
    # of mail exists at all; these decide whether this person gets it.
    email_weekly = models.BooleanField(
        default=True, help_text='The weekly picks-are-live email.')
    email_reminder = models.BooleanField(
        default=True, help_text='The nudge before picks lock.')
    score = models.FloatField(default=0)
    bio = models.TextField(max_length=300, blank=True, default='')
    real_name = models.CharField(max_length=50, blank=True, default='')
    favorite_team = models.CharField(max_length=50, choices=TEAMS, default='Arizona Cardinals')
    big_loser = models.CharField(max_length=50, choices=TEAMS, default='Arizona Cardinals')
    nfc_champ = models.CharField(max_length=50, choices=TEAMS, default='Arizona Cardinals')
    afc_champ = models.CharField(max_length=50, choices=TEAMS, default='Buffalo Bills')
    superbowl_winner = models.CharField(max_length=50, choices=TEAMS, default='Arizona Cardinals')
    is_bot = models.BooleanField(default=False)
    BOT_STRATEGY_CHOICES = [
        ('random', 'Random (uses underdog %)'),
        ('gemini', 'Gemini AI picks each game'),
    ]
    bot_strategy = models.CharField(
        max_length=20, choices=BOT_STRATEGY_CHOICES, default='random',
        help_text='How this bot decides its picks. Only applies when is_bot is set.',
    )
    bot_underdog_pct = models.IntegerField(default=50)
    preseason_submitted = models.BooleanField(default=False)
    # Whether mail from this member's address gets published to the site's Emails
    # feed. Commissioner-controlled from the Accounts dashboard, and off by
    # default: turning it on is granting someone write access to the home page.
    email_posts_enabled = models.BooleanField(
        default=False,
        help_text="Publish this member's league-wide emails on the site.",
    )

    def __str__(self):
        return self.user.username

    @property
    def score_display(self):
        return round(self.score, 1)

    @property
    def favorite_team_abbrev(self):
        """Three-letter form, for anywhere the full name will not fit.

        Falls back to the full name rather than blank: an unmapped team should
        look wrong, not look empty.
        """
        from main.teams import TEAM_ABBREV
        return TEAM_ABBREV.get(self.favorite_team, self.favorite_team)

    @property
    def display_name(self):
        """Real name if they set one, otherwise the username."""
        return self.real_name.strip() or self.user.username

    @property
    def is_manager(self):
        """Runs this league. Superusers run every league."""
        return self.role == 'manager' or self.user.is_superuser


@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)

