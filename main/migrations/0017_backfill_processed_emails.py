"""Seed ProcessedEmail from the messages already in the feed.

Dedupe used to read LeagueEmail. Without this backfill, every already-ingested
message still sitting in the mailbox would look unseen on the next poll — and a
published one would be **relayed to the whole league a second time**.

Site-generated rows are included too. Their ids are synthetic and can never match
an inbound Message-ID, so they cost nothing and keep the rule simple: everything
that has been handled is recorded.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    LeagueEmail = apps.get_model('main', 'LeagueEmail')
    ProcessedEmail = apps.get_model('main', 'ProcessedEmail')

    seen = set(ProcessedEmail.objects.values_list('message_id', flat=True))
    rows = [
        ProcessedEmail(message_id=mid, outcome='backfilled from the feed')
        for mid in LeagueEmail.objects.values_list('message_id', flat=True)
        if mid and mid not in seen
    ]
    ProcessedEmail.objects.bulk_create(rows, ignore_conflicts=True)


def backwards(apps, schema_editor):
    ProcessedEmail = apps.get_model('main', 'ProcessedEmail')
    ProcessedEmail.objects.filter(outcome='backfilled from the feed').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0016_processedemail'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
