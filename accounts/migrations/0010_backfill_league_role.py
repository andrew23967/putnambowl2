"""Every existing account joins the default league; staff become managers.

`is_staff` used to be the site's only notion of "runs the league", and it is a
site-wide flag - a manager of one league would have had the dashboards of every
league. Managing is now `Profile.role`; `is_staff` keeps its Django meaning
(access to /admin/) and is cleared for everyone who is not a superuser.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    League = apps.get_model('leagues', 'League')
    Profile = apps.get_model('accounts', 'Profile')
    User = apps.get_model('auth', 'User')
    league = League.objects.get(slug='putnambowl')
    Profile.objects.filter(league__isnull=True).update(league=league)
    Profile.objects.filter(user__is_staff=True).update(role='manager')
    User.objects.filter(is_staff=True, is_superuser=False).update(is_staff=False)


def backwards(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    User.objects.filter(profile__role='manager').update(is_staff=True)


class Migration(migrations.Migration):
    dependencies = [('accounts', '0009_profile_league_role_email_prefs'), ('leagues', '0002_default_league')]
    operations = [migrations.RunPython(forwards, backwards)]
