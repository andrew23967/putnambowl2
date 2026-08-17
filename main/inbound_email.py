"""Ingest league mail from the league mailbox into the Emails feed.

Everyone writes to one address. There is no mailing list: the site holds the
membership, so the commissioner sends one email here and `relay_to_league`
forwards it to every member.

Why IMAP rather than a provider webhook: it behaves identically in development
and production, needs no public URL or tunnel, works with any mailbox, and the
`run_auto` worker already runs on a tick.

## Who may publish, and what a message means

Two gates before anything happens:

1. **Authentication passes.** SPF/DKIM/DMARC, read from the receiving server's
   `Authentication-Results` header. This is the only real security boundary here
   — `From` is trivially forged, so without it anyone who knows the
   commissioner's address could publish to the site.
2. **The sender is a known member**, matched case-insensitively on `User.email`.

Then `_is_pick_submission` decides what the message *is*:

* Mail to the tagged **`+picks`** address is always a pick submission.
* Otherwise the sender's `profile.email_posts_enabled` decides — on means publish
  and relay to the league, off means read it as picks. Off by default, so most of
  the league sends picks and only the commissioner announces.

The tag override exists for a specific footgun: the commissioner *is* set to
publish, so without it a reply to their own ballot would broadcast their picks to
the whole league. Ballots set `Reply-To` to the tagged address, so replying is
unambiguous for everyone.

Every rejection is logged with its reason. Silent drops are what make inbound
mail miserable to debug.

## Configuration

    IMAP_HOST, IMAP_PORT (default 993), IMAP_USER, IMAP_PASSWORD
    IMAP_FOLDER          (default INBOX)
    IMAP_MARK_SEEN        (default true — stop re-reading the same mail)
    PICKS_ADDRESS_TAG     (default "picks" — the +tag meaning "these are my picks")
    INBOUND_REQUIRE_AUTH  (default true — only turn off against a local mailbox)
"""
import email
import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime

from django.conf import settings as django_settings
from django.contrib.auth.models import User

from .models import LeagueEmail, ProcessedEmail

log = logging.getLogger(__name__)

MAX_BODY_CHARS = 20000

# How far back each poll looks. Bounded so the work per tick stays constant, but
# wide enough to survive the worker being down for a few days.
INBOUND_WINDOW_DAYS = 7

# Trailing quoted replies and signatures, so a thread does not grow a copy of
# itself every time someone hits reply.
_QUOTE_MARKERS = [
    re.compile(r'^On .{5,80} wrote:\s*$', re.M),
    re.compile(r'^-{2,}\s*Original Message\s*-{2,}\s*$', re.M | re.I),
    re.compile(r'^_{5,}\s*$', re.M),
    re.compile(r'^From:\s.+$', re.M),
]


def _conf(name, default=None):
    return getattr(django_settings, name, None) or default


def _decode(raw):
    if not raw:
        return ''
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:
        return str(raw).strip()


def _plain_body(msg):
    """Prefer text/plain. Only fall back to stripping tags out of HTML."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/plain' and \
                    'attachment' not in str(part.get('Content-Disposition', '')):
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or 'utf-8', 'replace')
                except Exception:
                    continue
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                try:
                    html = part.get_payload(decode=True).decode(
                        part.get_content_charset() or 'utf-8', 'replace')
                except Exception:
                    continue
                text = re.sub(r'<br\s*/?>', '\n', html, flags=re.I)
                text = re.sub(r'</p\s*>', '\n\n', text, flags=re.I)
                return re.sub(r'<[^>]+>', '', text)
        return ''
    try:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or 'utf-8', 'replace')
    except Exception:
        return msg.get_payload() or ''


def _trim(body):
    cut = len(body)
    for pattern in _QUOTE_MARKERS:
        m = pattern.search(body)
        if m:
            cut = min(cut, m.start())
    body = body[:cut]
    # Collapse runs of blank lines left behind by the trim.
    body = re.sub(r'\n{3,}', '\n\n', body).strip()
    return body[:MAX_BODY_CHARS]


def _auth_ok(msg):
    """Did the receiving server verify the sender?

    Accept on an explicit DMARC or DKIM pass, or an SPF pass. Reject when results
    are present but all failing. A mailbox that adds no Authentication-Results at
    all cannot be judged, so treat that as a failure unless auth is disabled.
    """
    if not str(_conf('INBOUND_REQUIRE_AUTH', 'true')).lower() in ('1', 'true', 'yes'):
        return True, 'auth check disabled'
    results = ' '.join(msg.get_all('Authentication-Results') or []).lower()
    if not results:
        return False, 'no Authentication-Results header'
    for mech in ('dmarc=pass', 'dkim=pass', 'spf=pass'):
        if mech in results:
            return True, mech
    return False, f'no passing mechanism in: {results[:120]}'


def _league_addresses():
    """Members who could plausibly be on a league email: humans with addresses."""
    return {
        (u.email or '').strip().lower(): u
        for u in User.objects.select_related('profile')
                     .exclude(email='').exclude(profile__is_bot=True)
    }


def _addressed_to(msg):
    """Every address this message was sent to, including the delivery envelope.

    Delivered-To matters: it carries the tagged address even when a client rewrote
    the visible To.
    """
    headers = ((msg.get_all('To') or []) + (msg.get_all('Cc') or [])
               + (msg.get_all('Delivered-To') or []))
    return {addr.strip().lower() for _, addr in getaddresses(headers) if addr}


def _is_pick_submission(msg, author):
    """Picks, or an announcement for the league? Returns (is_picks, reason).

    The account's `email_posts_enabled` flag decides — it is what "publish this
    member's email" means — with one override: anything sent to the tagged picks
    address is picks regardless. That override is what lets the commissioner, whose
    plain mail is published, reply to a ballot without broadcasting their picks.
    """
    from .email_utils import picks_address

    tagged = (picks_address() or '').strip().lower()
    if tagged and tagged in _addressed_to(msg):
        return True, f'addressed to {tagged}'
    if not author.profile.email_posts_enabled:
        return True, f'{author.username} is not set to publish by email'
    return False, f'{author.username} is set to publish by email'


def ingest_message(raw_bytes):
    """Store one message if it passes every rule. Returns (obj_or_None, reason)."""
    msg = email.message_from_bytes(raw_bytes)

    message_id = (msg.get('Message-ID') or '').strip()
    if not message_id:
        return None, 'no Message-ID'
    # Dedupe against ProcessedEmail, not the feed: the feed can be deleted, and if
    # dedupe read from it, deleting a message still inside the poll window would
    # re-ingest and re-relay it to the whole league.
    if ProcessedEmail.objects.filter(message_id=message_id).exists():
        return None, 'already ingested'

    from_name, from_email = ('', '')
    parsed = getaddresses(msg.get_all('From') or [])
    if parsed:
        from_name, from_email = parsed[0]
    from_email = (from_email or '').strip().lower()
    if not from_email:
        return None, 'no From address'

    ok, detail = _auth_ok(msg)
    if not ok:
        return None, f'authentication failed ({detail})'

    members = _league_addresses()
    author = members.get(from_email)
    if author is None:
        return None, f'sender {from_email} is not a league member'

    try:
        sent_at = parsedate_to_datetime(msg.get('Date'))
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
    except Exception:
        sent_at = datetime.now(timezone.utc)

    full_body = _plain_body(msg)
    body = _trim(full_body)
    if not body:
        return None, 'empty body'

    is_picks, why = _is_pick_submission(msg, author)

    if is_picks:
        from . import pick_email
        try:
            # The untrimmed body on purpose: people reply by editing inside the
            # quoted original, so the edited ballot is often below the "On ...
            # wrote:" line that _trim() cuts off.
            outcome = pick_email.handle(
                author, full_body, reply_to=from_email,
                message_id=message_id, subject=_decode(msg.get('Subject')),
            )
        except Exception as e:
            log.exception('[inbound] pick parsing failed')
            # Not marked processed, so a transient failure gets another go.
            return None, f'pick parsing failed for {author.username}: {e}'
        ProcessedEmail.objects.get_or_create(
            message_id=message_id[:400], defaults={'outcome': outcome[:200]})
        # Kept as a record of what arrived, but out of the feed — picks are private
        # until the week locks.
        LeagueEmail.objects.create(
            author=author, from_email=from_email, from_name=_decode(from_name),
            subject=_decode(msg.get('Subject')) or '(no subject)',
            body=body, source=LeagueEmail.SOURCE_INBOUND, sent_at=sent_at,
            message_id=message_id[:400], recipient_count=0, published=False,
        )
        return None, f'{outcome} ({why})'

    obj = LeagueEmail.objects.create(
        author=author,
        from_email=from_email,
        from_name=_decode(from_name),
        subject=_decode(msg.get('Subject')) or '(no subject)',
        body=body,
        source=LeagueEmail.SOURCE_INBOUND,
        sent_at=sent_at,
        message_id=message_id[:400],
        recipient_count=len(_addressed_to(msg) & set(members)),
    )
    # Marked processed before relaying, so a message can never be forwarded to the
    # league twice — not on a later poll, and not if its feed row is deleted.
    ProcessedEmail.objects.get_or_create(
        message_id=message_id[:400], defaults={'outcome': 'published'})

    # Forward it on. The site holds the membership, so the commissioner sends one
    # email and everyone receives it — no mailing list to maintain.
    relayed = 0
    try:
        from .email_utils import relay_to_league
        relayed = relay_to_league(
            obj, sender_email=from_email, already_copied=_addressed_to(msg),
            author_name=_decode(from_name) or author.username,
        )
    except Exception as e:
        # The message is already on the site; a relay failure must not undo that.
        log.exception('[relay] forwarding failed')
        print(f'[relay] forwarding failed: {e}', flush=True)

    return obj, f'published ({why}), relayed to {relayed}'


def verify():
    """Check the mailbox settings without ingesting anything.

    Reports what the poller can actually see, so a setup problem shows up as a
    named cause — wrong host, bad app password, misspelled folder — instead of
    mail silently never appearing.
    """
    host, user, password = _conf('IMAP_HOST'), _conf('IMAP_USER'), _conf('IMAP_PASSWORD')
    folder = _conf('IMAP_FOLDER', 'INBOX')
    missing = [n for n, v in (('IMAP_HOST', host), ('IMAP_USER', user),
                              ('IMAP_PASSWORD', password)) if not v]
    if missing:
        return False, f'not configured — missing {", ".join(missing)}'

    try:
        with imaplib.IMAP4_SSL(host, int(_conf('IMAP_PORT', 993))) as imap:
            imap.login(user, password)
            typ, _ = imap.select(folder, readonly=True)
            if typ != 'OK':
                return False, f'logged in, but folder {folder!r} could not be opened'
            total = imap.search(None, 'ALL')[1][0].split()
            unseen = imap.search(None, 'UNSEEN')[1][0].split()
    except imaplib.IMAP4.error as e:
        return False, f'login/select rejected: {e}'
    except Exception as e:
        return False, f'could not connect to {host}: {e}'

    members = _league_addresses()
    notes = [
        f'connected to {host} as {user}',
        f'folder {folder}: {len(total)} message(s), {len(unseen)} unread',
        f'{len(members)} league member(s) with an address',
        f'{sum(1 for u in members.values() if u.profile.email_posts_enabled)} '
        f'allowed to publish by email',
    ]
    from .email_utils import picks_address
    notes.append(f'announcements to: {user}')
    notes.append(f'picks to:         {picks_address()}')
    if not str(_conf('INBOUND_REQUIRE_AUTH', 'true')).lower() in ('1', 'true', 'yes'):
        notes.append('WARNING: INBOUND_REQUIRE_AUTH is off — a forged From header '
                     'is enough to publish to the site')
    return True, '\n  '.join(notes)


def fetch(limit=25):
    """Poll the mailbox once. Returns (stored, skipped). Never raises."""
    host = _conf('IMAP_HOST')
    user = _conf('IMAP_USER')
    password = _conf('IMAP_PASSWORD')
    if not (host and user and password):
        log.debug('[inbound] IMAP not configured — skipping')
        return 0, 0

    folder = _conf('IMAP_FOLDER', 'INBOX')
    port = int(_conf('IMAP_PORT', 993))
    mark_seen = str(_conf('IMAP_MARK_SEEN', 'true')).lower() in ('1', 'true', 'yes')

    stored = skipped = 0
    try:
        with imaplib.IMAP4_SSL(host, port) as imap:
            imap.login(user, password)
            imap.select(folder)
            # Search a recent window rather than UNSEEN. This is a real mailbox a
            # person can open, and reading a message in the web client clears its
            # unread flag — which, with an UNSEEN search, meant the poller would
            # skip that message for ever. Re-reading is free because message_id is
            # unique, so an already-stored message is simply recognised.
            since = (datetime.now(timezone.utc) - timedelta(days=INBOUND_WINDOW_DAYS))
            typ, data = imap.search(None, 'SINCE', since.strftime('%d-%b-%Y'))
            if typ != 'OK':
                log.warning('[inbound] search failed: %s', typ)
                return 0, 0
            # Newest first, so a backlog longer than the limit still gets the
            # messages that matter.
            ids = list(reversed((data[0] or b'').split()))[:limit]
            for num in ids:
                typ, payload = imap.fetch(num, '(BODY.PEEK[])')
                if typ != 'OK' or not payload or not isinstance(payload[0], tuple):
                    skipped += 1
                    continue
                try:
                    obj, reason = ingest_message(payload[0][1])
                except Exception as e:
                    obj, reason = None, f'error: {e}'
                    log.exception('[inbound] ingest error')
                if obj:
                    stored += 1
                    print(f'[inbound] stored "{obj.subject}" from {obj.from_email} '
                          f'— {reason}', flush=True)
                else:
                    skipped += 1
                    print(f'[inbound] skipped a message — {reason}', flush=True)
                if mark_seen:
                    imap.store(num, '+FLAGS', '\\Seen')
    except Exception as e:
        # Best-effort by design: this runs inside the worker tick, and a mailbox
        # outage must never stop the league from being scraped and graded.
        log.error('[inbound] poll failed: %s', e)
        print(f'[inbound] poll failed: {e}', flush=True)
        return stored, skipped

    if stored or skipped:
        print(f'[inbound] {stored} stored, {skipped} skipped', flush=True)
    return stored, skipped
