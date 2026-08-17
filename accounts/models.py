from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from main.teams import TEAMS, NFC_TEAMS, AFC_TEAMS


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    score = models.FloatField(default=0)
    bio = models.TextField(max_length=300, blank=True, default='')
    real_name = models.CharField(max_length=50, blank=True, default='')
    theme = models.CharField(max_length=20, default='#00897b')
    favorite_team = models.CharField(max_length=50, choices=TEAMS, default='Arizona Cardinals')
    big_loser = models.CharField(max_length=50, choices=TEAMS, default='Arizona Cardinals')
    nfc_champ = models.CharField(max_length=50, choices=TEAMS, default='Arizona Cardinals')
    afc_champ = models.CharField(max_length=50, choices=TEAMS, default='Buffalo Bills')
    superbowl_winner = models.CharField(max_length=50, choices=TEAMS, default='Arizona Cardinals')
    unread_messages = models.IntegerField(default=0)
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


@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_profile(sender, instance, **kwargs):
    if hasattr(instance, 'profile'):
        instance.profile.save()
