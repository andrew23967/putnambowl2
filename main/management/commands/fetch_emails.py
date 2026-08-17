"""Poll the league mailbox once, by hand.

    python manage.py fetch_emails
    python manage.py fetch_emails --file message.eml   # no mailbox needed

The worker does this every tick; this command is for setting it up and for
diagnosing why a message did not appear. Both paths print the reason each message
was stored or skipped.
"""
from django.core.management.base import BaseCommand

from main import inbound_email


class Command(BaseCommand):
    help = 'Fetch league email from the configured IMAP mailbox into the Emails feed.'

    def add_arguments(self, parser):
        parser.add_argument('--file', help='Ingest a .eml file instead of polling IMAP.')
        parser.add_argument('--check', action='store_true',
                            help='Test the mailbox settings without ingesting anything.')
        parser.add_argument('--limit', type=int, default=25,
                            help='Maximum messages to read this run (default 25).')

    def handle(self, *args, **options):
        if options['check']:
            ok, detail = inbound_email.verify()
            style = self.style.SUCCESS if ok else self.style.ERROR
            self.stdout.write(style(('OK: ' if ok else 'FAILED: ') + detail))
            return

        if options['file']:
            with open(options['file'], 'rb') as fh:
                obj, reason = inbound_email.ingest_message(fh.read())
            if obj:
                self.stdout.write(self.style.SUCCESS(
                    f'stored "{obj.subject}" from {obj.from_email} — {reason}'))
            else:
                self.stdout.write(self.style.WARNING(f'not stored — {reason}'))
            return

        if not inbound_email._conf('IMAP_HOST'):
            self.stdout.write(self.style.WARNING(
                'IMAP_HOST is not set — inbound email is disabled. '
                'Set IMAP_HOST/IMAP_USER/IMAP_PASSWORD to enable it.'))
            return

        stored, skipped = inbound_email.fetch(limit=options['limit'])
        self.stdout.write(f'{stored} stored, {skipped} skipped')
