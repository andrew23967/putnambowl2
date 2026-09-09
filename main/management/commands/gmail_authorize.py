"""One-time OAuth grant so the site can send from the league mailbox over the
Gmail API (docs/email.md). Prints the refresh token to put in the environment.

    python manage.py gmail_authorize --client-id ... --client-secret ...
    python manage.py gmail_authorize --test-to you@example.com   # once configured
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Authorize the Gmail API for the league mailbox, or send a test message through it.'

    def add_arguments(self, parser):
        parser.add_argument('--client-id', default=settings.GMAIL_CLIENT_ID)
        parser.add_argument('--client-secret', default=settings.GMAIL_CLIENT_SECRET)
        parser.add_argument('--no-browser', action='store_true',
                            help='Print the consent URL instead of opening it.')
        parser.add_argument('--test-to', metavar='ADDRESS',
                            help='Skip the grant; send one test message with the configured credentials.')

    def handle(self, *args, **opts):
        from main import gmail_api

        if opts['test_to']:
            if not gmail_api.configured():
                raise CommandError('GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET and GMAIL_REFRESH_TOKEN must be set.')
            ok, why = gmail_api.send(opts['test_to'], 'PutnamBowl test',
                                     'Sent from the league mailbox over the Gmail API.')
            if not ok:
                raise CommandError(f'send failed: {why}')
            self.stdout.write(f'Sent to {opts["test_to"]}.')
            return

        if not (opts['client_id'] and opts['client_secret']):
            raise CommandError('Pass --client-id and --client-secret (a Desktop OAuth client '
                               'from Google Cloud Console with the Gmail API enabled).')
        token = gmail_api.authorize(opts['client_id'], opts['client_secret'],
                                    open_browser=not opts['no_browser'])
        self.stdout.write('\nAuthorized. Set these on BOTH Railway services:\n\n'
                          f'  EMAIL_TRANSPORT=gmail\n'
                          f'  GMAIL_CLIENT_ID={opts["client_id"]}\n'
                          f'  GMAIL_CLIENT_SECRET={opts["client_secret"]}\n'
                          f'  GMAIL_REFRESH_TOKEN={token}\n')
