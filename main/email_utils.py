import logging
import threading
from datetime import datetime, timezone

from django.conf import settings as django_settings
from django.contrib.auth.models import User

log = logging.getLogger(__name__)


def _format_lock_delta(lock_dt):
    now = datetime.now(timezone.utc)
    delta = lock_dt - now
    if delta.total_seconds() <= 0:
        return 'very soon'
    total_minutes = int(delta.total_seconds() // 60)
    days = total_minutes // (60 * 24)
    hours = (total_minutes % (60 * 24)) // 60
    minutes = total_minutes % 60
    parts = []
    if days:
        parts.append(f'{days} day{"s" if days != 1 else ""}')
    if hours:
        parts.append(f'{hours} hour{"s" if hours != 1 else ""}')
    if not days and minutes:
        parts.append(f'{minutes} minute{"s" if minutes != 1 else ""}')
    return ' '.join(parts) or 'very soon'


def _format_lock_dt(lock_dt, tz_str='UTC'):
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_str or 'UTC')
    except Exception:
        tz = timezone.utc
    local = lock_dt.astimezone(tz)
    tz_label = tz_str.replace('_', ' ').split('/')[-1] if tz_str else 'UTC'
    hour = local.hour % 12 or 12
    minute = local.strftime('%M')
    ampm = 'AM' if local.hour < 12 else 'PM'
    return f'{local.strftime("%A")} at {hour}:{minute} {ampm} {tz_label}'


def build_ballot(games):
    """The week's games as a list to edit down, one line per game.

    Members reply having deleted the team they don't want, which leaves the winner
    — no typing, which is the point for anyone who finds the site awkward.
    `pick_email.parse_ballot` reads the result line by line.

    Favourite first, matching the team1/team2 convention, with the points so the
    underdog premium is visible while choosing.
    """
    if not games:
        return ''
    lines = [
        '── Reply with your picks ──────────────────────────',
        '',
        "Reply to this email and delete the team you DON'T want from each",
        'line, leaving the one you think will win. Send it back and I will',
        'record them. You do not have to do them all at once.',
        '',
    ]
    for game in games:
        lines.append(f'  {game.team1} ({game.points1})  /  {game.team2} ({game.points2})')
    lines += [
        '',
        'The second team in each line is the underdog and worth more.',
    ]
    return '\n'.join(lines)


def record_site_email(subject, body, recipient_count, author=None, sent_at=None,
                      slug=None):
    """Put a message the site sent into the Emails feed.

    Recorded at send time rather than ingested back out of the mailbox, so the
    site's own mail appears even when inbound polling is unconfigured or broken.

    `slug` makes the synthetic Message-ID stable and unique, which is what stops
    a re-send — a regenerated recap, say — from creating a second row.
    """
    from .models import LeagueEmail

    sent_at = sent_at or datetime.now(timezone.utc)
    site_url = getattr(django_settings, 'SITE_URL', 'localhost')
    domain = site_url.split('//')[-1].strip('/') or 'putnambowl.local'
    key = slug or sent_at.strftime('%Y%m%d%H%M%S')
    message_id = f'<site-{key}@{domain}>'

    obj, created = LeagueEmail.objects.update_or_create(
        message_id=message_id,
        defaults={
            'author': author,
            'from_name': getattr(author, 'username', '') or 'PutnamBowl',
            'from_email': getattr(django_settings, 'RESEND_FROM', '') or '',
            'subject': subject,
            'body': body,
            'source': LeagueEmail.SOURCE_SITE,
            'sent_at': sent_at,
            'recipient_count': recipient_count,
        },
    )
    print(f'[email] recorded to feed ({"new" if created else "updated"}): {subject}',
          flush=True)
    return obj, created


# PutnamBot signs its own work. The feed shows an author either way, but the mail
# that goes out has no such framing, so it says so in the body.
PUTNAMBOT_SIGNOFF = (
    "——\n"
    "I'm PutnamBot, the AI commissioner of this league. This recap is mine — "
    "I write one after every week is scored."
)


def league_recipients():
    """Everyone who should get league mail: real members with an address."""
    return list(
        User.objects.exclude(email='').exclude(email__isnull=True)
        .exclude(profile__is_bot=True)
        .values_list('email', flat=True)
    )


def record_recap_email(week, recap_text, recipient_count=0, subject=None):
    """Record one of PutnamBot's recaps in the Emails feed, without sending it.

    Keyed on the week, so regenerating a recap replaces its row instead of
    stacking up duplicates. Returns (obj, created).

    For the normal path use `send_recap_email` — this is for corrections, where
    the league has already been mailed and should not be mailed again.
    """
    if not (recap_text or '').strip():
        return None, False
    author = User.objects.filter(username='putnambot').first()
    return record_site_email(
        subject=subject or f'Week {week} recap',
        body=f'{recap_text.strip()}\n\n{PUTNAMBOT_SIGNOFF}',
        recipient_count=recipient_count,
        author=author,
        slug=f'recap-w{week}' if week else 'season-preview',
    )


def send_recap_email(week, recap_text, subject=None):
    """Mail one of PutnamBot's recaps to the league, and record it in the feed.

    PutnamBot's own intro promises "a comprehensive recap" by email, and for a
    while it didn't send one: recaps were recorded to the feed and only ever
    reached an inbox second-hand, as a "Last Week" section inside the next
    "picks are live" mail.

    Sends only when the feed row is newly created. The slug makes that check
    idempotent, so advancing a week twice — or a retried worker tick — cannot
    mail the league the same recap again.
    """
    if not (recap_text or '').strip():
        return False

    recipients = league_recipients()
    obj, created = record_recap_email(week, recap_text, recipient_count=len(recipients),
                                      subject=subject)
    if obj is None:
        return False
    if not created:
        print(f'[email] recap "{obj.subject}" already sent — not resending', flush=True)
        return False

    api_key = getattr(django_settings, 'RESEND_API_KEY', '')
    if not api_key:
        print('[email] RESEND_API_KEY not set — recap recorded but not emailed',
              flush=True)
        return False
    if not recipients:
        print('[email] no recipients with an address — recap recorded but not emailed',
              flush=True)
        return False

    from_email = getattr(django_settings, 'RESEND_FROM', 'onboarding@resend.dev')
    inbox = getattr(django_settings, 'IMAP_USER', '') or ''
    site_url = getattr(django_settings, 'SITE_URL', 'http://localhost:8000')
    body = (f'{obj.body}\n\n'
            f'Standings and the full archive: {site_url.rstrip("/")}/emails/')

    def _send():
        try:
            import resend
            resend.api_key = api_key
        except Exception as e:
            print(f'[email] FAILED to init resend: {e}', flush=True)
            return
        sent = 0
        for address in recipients:
            try:
                payload = {'from': from_email, 'to': [address],
                           'subject': obj.subject, 'text': body}
                if inbox:
                    payload['reply_to'] = [inbox]
                resend.Emails.send(payload)
                sent += 1
            except Exception as e:
                print(f'[email] recap FAILED for one recipient: {e}', flush=True)
        print(f'[email] recap "{obj.subject}" sent to {sent}/{len(recipients)}',
              flush=True)

    threading.Thread(target=_send, daemon=True).start()
    return True


def send_picks_published_email(site_settings):
    """Send weekly picks-live notification to all non-bot users with an email address."""
    api_key = getattr(django_settings, 'RESEND_API_KEY', '')
    print(f'[email] send_picks_published_email called, week={site_settings.week}', flush=True)
    if not api_key:
        print('[email] RESEND_API_KEY not set — skipping.', flush=True)
        return

    recipients = list(
        User.objects.filter(email__isnull=False)
        .exclude(email='')
        .exclude(profile__is_bot=True)
        .values_list('email', flat=True)
    )
    print(f'[email] {len(recipients)} recipient(s)', flush=True)
    if not recipients:
        print('[email] No recipients — skipping.', flush=True)
        return

    week = site_settings.week
    site_url = getattr(django_settings, 'SITE_URL', 'http://localhost:8000')
    from_email = getattr(django_settings, 'RESEND_FROM', 'onboarding@resend.dev')
    picks_url = f'{site_url.rstrip("/")}/picks/'

    # Replies must land in the mailbox the worker polls, not wherever RESEND_FROM
    # points — otherwise an edited ballot goes to an address nobody reads and the
    # member hears nothing back.
    inbox = getattr(django_settings, 'IMAP_USER', '') or ''

    from .models import Game
    games = list(Game.objects.filter(week=week))
    games.sort(key=lambda g: (g.game_dt is None, g.game_dt, g.id))
    ballot = build_ballot(games) if inbox else ''
    if games and not inbox:
        print('[email] IMAP_USER not set — sending without a reply-by-email '
              'ballot, since replies would go nowhere', flush=True)

    lock_line = ''
    if site_settings.auto_lock_dt:
        time_left = _format_lock_delta(site_settings.auto_lock_dt)
        lock_when = _format_lock_dt(site_settings.auto_lock_dt, site_settings.auto_tz)
        lock_line = f'Picks lock in {time_left} ({lock_when}). Get yours in before then.\n'
    elif site_settings.first_game_dt:
        lock_line = 'Picks lock before the first kickoff.\n'

    recap_section = ''
    if site_settings.weekly_recap:
        recap_section = f'\n── Last Week ─────────────────────────────────\n\n{site_settings.weekly_recap}\n'

    subject = f'Week {week} picks are live'
    ballot_section = f'\n{ballot}\n' if ballot else ''
    body = (
        f'Week {week} picks are up.\n\n'
        f'{lock_line}'
        f'\nMake your picks on the site: {picks_url}\n'
        f'{ballot_section}'
        f'{recap_section}'
        f'\n\n──\nPutnamBowl'
    )

    # Into the feed before the send thread starts: the page should show what the
    # league was told even if Resend then fails on every address.
    record_site_email(
        subject=subject, body=body, recipient_count=len(recipients),
        slug=f'picks-live-w{week}',
    )

    def _send():
        # One message per recipient. Putting the whole league in a single `to`
        # would disclose every member's address to everyone else.
        try:
            import resend
            resend.api_key = api_key
        except Exception as e:
            print(f'[email] FAILED to init resend: {e}', flush=True)
            return

        sent = 0
        for address in recipients:
            try:
                payload = {
                    'from': from_email,
                    'to': [address],
                    'subject': subject,
                    'text': body,
                }
                if inbox:
                    payload['reply_to'] = [inbox]
                resend.Emails.send(payload)
                sent += 1
            except Exception as e:
                print(f'[email] FAILED for one recipient: {e}', flush=True)
        print(f'[email] sent OK to {sent}/{len(recipients)} recipients for week {week}', flush=True)

    threading.Thread(target=_send, daemon=True).start()
