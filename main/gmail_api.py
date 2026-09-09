"""Send from the league mailbox over the Gmail API.

Railway blocks outbound SMTP on every plan below Pro, so the mailbox cannot be
used the plain way from there. The Gmail API is HTTPS, which is not blocked,
and a message sent through it is indistinguishable from one sent over SMTP:
same From, same Sent folder, same threading headers.

It needs a one-time OAuth grant from the mailbox's owner (see the
`gmail_authorize` command), which yields a refresh token. That token plus the
OAuth client id/secret go in the environment; access tokens are minted from it
here and cached for their hour.
"""
import base64
import logging
import time
from email.message import EmailMessage

import requests
from django.conf import settings as django_settings

log = logging.getLogger(__name__)

SCOPE = 'https://www.googleapis.com/auth/gmail.send'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
SEND_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'

_token = {'value': '', 'expires': 0.0}


def _conf(name):
    return getattr(django_settings, name, '') or ''


def configured():
    """All three credentials present."""
    return all((_conf('GMAIL_CLIENT_ID'), _conf('GMAIL_CLIENT_SECRET'),
                _conf('GMAIL_REFRESH_TOKEN')))


def _access_token():
    """A bearer token, refreshed a minute before it would expire."""
    if _token['value'] and time.time() < _token['expires'] - 60:
        return _token['value']
    r = requests.post(TOKEN_URL, data={
        'client_id': _conf('GMAIL_CLIENT_ID'),
        'client_secret': _conf('GMAIL_CLIENT_SECRET'),
        'refresh_token': _conf('GMAIL_REFRESH_TOKEN'),
        'grant_type': 'refresh_token',
    }, timeout=20)
    r.raise_for_status()
    data = r.json()
    _token['value'] = data['access_token']
    _token['expires'] = time.time() + int(data.get('expires_in', 3600))
    return _token['value']


def send(to, subject, body, in_reply_to=None, reply_to=None):
    """One message from the mailbox. Returns (ok, why), like send_via_mailbox."""
    sender = _conf('SMTP_USER') or _conf('IMAP_USER')
    if not (configured() and sender):
        return False, 'Gmail API not configured'

    msg = EmailMessage()
    msg['From'] = sender
    msg['To'] = to
    msg['Subject'] = subject
    if reply_to:
        msg['Reply-To'] = reply_to
    if in_reply_to:
        msg['In-Reply-To'] = in_reply_to
        msg['References'] = in_reply_to
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('ascii')

    try:
        r = requests.post(SEND_URL, json={'raw': raw},
                          headers={'Authorization': f'Bearer {_access_token()}'},
                          timeout=30)
        if r.status_code >= 400:
            log.error('[email] Gmail API send to %s failed: %s %s', to, r.status_code, r.text[:300])
            return False, f'{r.status_code}: {r.text[:200]}'
        log.info(f'[email] sent via Gmail API to {to}: {subject}')
        return True, 'sent'
    except Exception as e:
        log.error('[email] Gmail API send to %s failed: %s', to, e)
        return False, str(e)


def authorize(client_id, client_secret, open_browser=True):
    """The one-time grant: opens the consent page, catches the redirect on
    localhost, exchanges the code. Returns the refresh token."""
    import http.server
    import socket
    import threading
    import urllib.parse
    import webbrowser

    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    redirect = f'http://127.0.0.1:{port}/'
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got['code'] = q.get('code', [''])[0]
            got['error'] = q.get('error', [''])[0]
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Done. You can close this tab.' if got['code']
                             else b'No code came back; see the terminal.')

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(('127.0.0.1', port), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    url = AUTH_URL + '?' + urllib.parse.urlencode({
        'client_id': client_id, 'redirect_uri': redirect, 'response_type': 'code',
        'scope': SCOPE, 'access_type': 'offline', 'prompt': 'consent',
    })
    print('Open this in a browser, signed in as the league mailbox:\n\n  ' + url + '\n')
    if open_browser:
        webbrowser.open(url)
    for _ in range(600):
        if got:
            break
        time.sleep(0.5)
    if not got.get('code'):
        raise SystemExit('No authorization code received: ' + (got.get('error') or 'timed out'))

    r = requests.post(TOKEN_URL, data={
        'client_id': client_id, 'client_secret': client_secret, 'code': got['code'],
        'grant_type': 'authorization_code', 'redirect_uri': redirect,
    }, timeout=20)
    r.raise_for_status()
    data = r.json()
    if 'refresh_token' not in data:
        raise SystemExit('Google returned no refresh token. Revoke the app at '
                         'https://myaccount.google.com/permissions and run this again.')
    return data['refresh_token']
