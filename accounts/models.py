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
    bot_preference = models.CharField(max_length=10, choices=[('underdog', 'Underdog'), ('favorite', 'Favorite')], blank=True, default='')
    preseason_submitted = models.BooleanField(default=False)

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
