"""Leagues.

Every account belongs to exactly one league, and every Game, Pick (through its
game), WeeklyLeaderboard, LeagueEmail, IntroTemplate and SeasonRecord carries a
league. The one row of `SiteSettings` the site used to run on is now one
`LeagueSettings` row per league - see `main/models.py`.

Nothing derives a league from global state: views take it from the signed-in
user's profile (`leagues/access.py`), the worker loops over the active leagues,
and the mailbox routes an inbound message by the sender's account.
"""
import secrets

from django.db import models

# No 0/O or 1/I, so a code read out over the phone cannot be misheard.
_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def new_join_code():
    return ''.join(secrets.choice(_CODE_ALPHABET) for _ in range(8))


class League(models.Model):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=40, unique=True)
    # Shown to the manager, typed by a new member on the create-account page.
    # Rotating it invalidates every link that carried the old one.
    join_code = models.CharField(max_length=12, unique=True, default=new_join_code)
    # Plain text, paragraphs separated by blank lines. Each league writes its own.
    rules = models.TextField(blank=True, default='')
    # Leagues are deactivated, never deleted: Profile.league is PROTECT.
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def rotate_join_code(self):
        self.join_code = new_join_code()
        self.save(update_fields=['join_code'])
        return self.join_code

    @property
    def settings(self):
        """This league's `LeagueSettings` row, created on first use."""
        from main.models import LeagueSettings
        return LeagueSettings.for_league(self)

    @property
    def members(self):
        from django.contrib.auth.models import User
        return User.objects.filter(profile__league=self).select_related('profile')

    @property
    def managers(self):
        return self.members.filter(profile__role='manager')
