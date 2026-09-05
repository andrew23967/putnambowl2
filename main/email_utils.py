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


def relay_to_league(league_email, sender_email, already_copied=(), author_name=''):
    """Forward a league-wide email on to every member.

    The site holds the real membership, the Google Group does not — most of the
    league is not in it. So one message to the group reaches the site, and the site
    reaches everyone. That is what makes this the league's mailer rather than just
    its archive.

    Skips the sender, and anyone already on the original To/Cc, so nobody gets it
    twice when the commissioner copies some people directly.

    `Reply-To` is the original sender, not the site's mailbox — deliberately. A
    reply landing back in our mailbox would be read as a *pick submission*, since
    that is what direct mail means; pointing replies at the commissioner both
    avoids that and is what someone hitting reply expects.
    """
    from .models import LeagueSettings
    league = league_email.league
    if not LeagueSettings.for_league(league).email_relay:
        log.info('[relay] forwarding switched off on the Emails page')
        return 0

    copied = {a.lower() for a in already_copied if a}
    copied.add((sender_email or '').lower())
    mailbox = (getattr(django_settings, 'SMTP_USER', '') or '').lower()
    if mailbox:
        copied.add(mailbox)

    recipients = [a for a in league_recipients(league) if a.lower() not in copied]
    if not recipients:
        log.info('[relay] nobody to forward to - everyone was already copied')
        return 0

    site_url = getattr(django_settings, 'SITE_URL', 'http://localhost:8000')
    who = author_name or sender_email
    body = (f'{league_email.body}\n\n'
            f'—\n'
            f'Sent to the league by {who} via {league.name}. '
            f'Reply to reach {sender_email}.\n'
            f'{site_url.rstrip("/")}/home/')

    def _send():
        sent = sum(1 for a in recipients
                   if send_via_mailbox(a, league_email.subject, body,
                                       reply_to=sender_email)[0])
        log.info(f'[relay] "{league_email.subject}" forwarded to '
              f'{sent}/{len(recipients)} member(s)')

    threading.Thread(target=_send, daemon=True).start()
    return len(recipients)


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


def record_site_email(league, subject, body, recipient_count, author=None,
                      sent_at=None, slug=None):
    """Put a message the site sent into the Emails feed.

    Recorded at send time rather than ingested back out of the mailbox, so the
    site's own mail appears even when inbound polling is unconfigured or broken.

    A `slug` makes the Message-ID stable, so re-recording the same thing replaces
    its row instead of adding another — a regenerated week 3 recap should update
    week 3's entry. Without one, every call gets a fresh unique id and therefore
    its own row.

    Note this is a *storage* key only. It used to gate sending too, and that was a
    mistake: it silently blocked real emails — a season preview, and week 1 of any
    second season — to prevent a duplicate that never actually happened.
    """
    import uuid

    from .models import LeagueEmail

    sent_at = sent_at or datetime.now(timezone.utc)
    site_url = getattr(django_settings, 'SITE_URL', 'localhost')
    domain = site_url.split('//')[-1].strip('/') or 'putnambowl.local'
    key = slug or uuid.uuid4().hex
    message_id = f'<site-{key}@{domain}>'

    obj, created = LeagueEmail.objects.update_or_create(
        message_id=message_id,
        defaults={
            'league': league,
            'author': author,
            'from_name': getattr(author, 'username', '') or league.name,
            'from_email': getattr(django_settings, 'RESEND_FROM', '') or '',
            'subject': subject,
            'body': body,
            'source': LeagueEmail.SOURCE_SITE,
            'sent_at': sent_at,
            'recipient_count': recipient_count,
        },
    )
    log.info(f'[email] recorded to feed ({"new" if created else "updated"}): {subject}')
    return obj, created


# League mail is from the commissioner, full stop. Recaps used to append a
# signoff introducing an "AI commissioner", and the feed credited them
# to the `putnambot` account - so the league had two commissioners, one of whom
# was a bot that also competed in the standings. PutnamBot is still a *player*;
# it is not a correspondent.
def signoff(league):
    return f"──\n{league.name}"


def picks_address():
    """The tagged address that means "these are my picks".

    Gmail delivers `user+picks@gmail.com` to `user@gmail.com` and keeps the tag in
    the headers, so one mailbox serves both purposes and the league needs no
    mailing list. Ballots and confirmations set Reply-To to this, which is what
    makes replying to one unambiguous — including for someone whose plain mail is
    published, where a reply would otherwise broadcast their picks.
    """
    return _tagged_address(
        getattr(django_settings, 'PICKS_ADDRESS_TAG', 'picks') or 'picks')


def _tagged_address(tag):
    """`user+tag@domain` for the league mailbox, or the bare mailbox if it cannot
    be built. Gmail delivers the tag to the same inbox and keeps it in the
    headers, which is what lets one mailbox route several jobs."""
    mailbox = getattr(django_settings, 'SMTP_USER', '') or \
        getattr(django_settings, 'IMAP_USER', '') or ''
    if not mailbox or '@' not in mailbox or not tag:
        return mailbox
    local, domain = mailbox.rsplit('@', 1)
    local = local.split('+', 1)[0]
    return f'{local}+{tag}@{domain}'


def intro_address():
    """The tagged address that means "this is this week's intro".

    Mail here from a member whose posting is enabled becomes
    `LeagueSettings.weekly_intro`, so the commissioner can write the week's opening
    line from their phone. The recap and the ballot are appended by the send path
    as usual - the intro is only ever the top section.
    """
    return _tagged_address(
        getattr(django_settings, 'INTRO_ADDRESS_TAG', 'intro') or 'intro')


def outbound_suppressed():
    """True when nothing should actually leave the building.

    This module talks to Resend and smtplib directly instead of going through
    Django's mail framework, so the test runner's locmem backend gives no
    protection. The suite really did deliver mail to fixture addresses like
    boss@example.com once SMTP was configured — checked here so a single guard
    covers every transport.
    """
    return bool(getattr(django_settings, 'TESTING', False))


def smtp_ready():
    """Whether the league mailbox can send."""
    if outbound_suppressed():
        return False
    return all((
        getattr(django_settings, 'SMTP_HOST', ''),
        getattr(django_settings, 'SMTP_USER', ''),
        getattr(django_settings, 'SMTP_PASSWORD', ''),
    ))


def send_via_mailbox(to, subject, body, in_reply_to=None, reply_to=None):
    """Send from the league mailbox over SMTP.

    Preferred over Resend, and the reason is not cosmetic: Resend's sandbox sender
    only delivers to the account owner until a domain is verified, so it could not
    reach a single league member. The Gmail app password already used for IMAP
    sends without any of that.

    It also makes a confirmation a real reply — same From address the member wrote
    to, and threaded via In-Reply-To/References so it appears in the conversation
    they started rather than as a stray new message.
    """
    import smtplib
    from email.message import EmailMessage

    if outbound_suppressed():
        return False, 'suppressed (tests)'

    host = getattr(django_settings, 'SMTP_HOST', '')
    port = int(getattr(django_settings, 'SMTP_PORT', 587))
    user = getattr(django_settings, 'SMTP_USER', '')
    password = getattr(django_settings, 'SMTP_PASSWORD', '')
    if not (host and user and password):
        return False, 'SMTP not configured'

    msg = EmailMessage()
    msg['From'] = user
    msg['To'] = to
    msg['Subject'] = subject
    if reply_to:
        msg['Reply-To'] = reply_to
    if in_reply_to:
        msg['In-Reply-To'] = in_reply_to
        msg['References'] = in_reply_to
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        log.info(f'[email] sent via mailbox to {to}: {subject}')
        return True, 'sent'
    except Exception as e:
        log.error('[email] SMTP send to %s failed: %s', to, e)
        return False, str(e)


def league_recipients(league, weekly=False):
    """Everyone in a league who should get its mail: real members with an address.

    `weekly=True` is the picks-are-live mail and honours the member's own
    opt-out; league correspondence relayed from the commissioner does not.

    One address per person, not one per account. Three accounts share
    agvdog@gmail.com, so without this that inbox received three copies of every
    email the league sent. Compared case-insensitively, since addresses are, and
    kept in account order so the list is stable.
    """
    qs = (User.objects.filter(profile__league=league)
          .exclude(email='').exclude(email__isnull=True)
          .exclude(profile__is_bot=True))
    if weekly:
        qs = qs.filter(profile__email_weekly=True)
    seen = set()
    out = []
    for address in qs.order_by('id').values_list('email', flat=True):
        key = address.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(address.strip())
    return out


def recap_slug(league, week, year=None):
    """Storage key for a *weekly* recap: one row per league, season and week.

    So regenerating week 3's recap replaces week 3's entry rather than leaving two
    versions in the feed. A season preview gets no slug — there is no natural "one
    per" for it, and every start of a season is its own event.
    """
    if not week:
        return None
    if year is None:
        from . import scrape
        year = scrape.current_season_year()
    return f'{league.slug}-recap-{year}-w{week}'


def record_recap_email(league, week, recap_text, recipient_count=0, subject=None,
                       year=None, slug=None):
    """Record a weekly recap in the Emails feed, without sending it.

    Keyed per season and week, so regenerating a recap replaces its row instead of
    stacking up duplicates. Returns (obj, created).

    The recap reaches the league inside the next picks-are-live mail; nothing
    mails it on its own.
    """
    if not (recap_text or '').strip():
        return None, False
    # No author: the recap is the league's own, the same as the picks-are-live
    # mail. Crediting the `putnambot` account put a robot avatar on it in the
    # feed and read as a second commissioner.
    return record_site_email(
        league,
        subject=subject or f'Week {week} recap',
        body=f'{recap_text.strip()}\n\n{signoff(league)}',
        recipient_count=recipient_count,
        slug=slug or recap_slug(league, week, year),
    )


def members_missing_picks(league, week):
    """(user, made, total) for everyone whose ballot is still incomplete.

    Incomplete, not merely empty. The rules are all-or-nothing - "if we do not
    receive all of your picks on time, then NONE of your picks will count" - so
    someone who did twelve of sixteen games is in exactly as much trouble as
    someone who did none, and is the person most worth nudging.
    """
    from .models import Game, Pick

    total = Game.objects.filter(league=league, week=week).count()
    if not total:
        return []

    made = {}
    for user_id in (Pick.objects.filter(game__league=league, game__week=week)
                    .values_list('user_id', flat=True)):
        made[user_id] = made.get(user_id, 0) + 1

    out = []
    for user in (User.objects.filter(profile__league=league, profile__email_reminder=True)
                 .exclude(email='').exclude(email__isnull=True)
                 .exclude(profile__is_bot=True).select_related('profile')):
        n = made.get(user.id, 0)
        if n < total:
            out.append((user, n, total))
    return out


def send_pick_reminder_email(site_settings):
    """Nudge anyone whose picks are still incomplete, once per week.

    Sent to individuals, never the league: it names how many games each person
    still owes, and that is nobody else's business.
    """
    week = site_settings.week
    league = site_settings.league
    if not site_settings.email_reminder:
        log.info('[email] reminder switched off on the Emails page.')
        return 0
    if site_settings.reminder_sent_week == week:
        return 0
    if outbound_suppressed() or not (smtp_ready()
                                     or getattr(django_settings, 'RESEND_API_KEY', '')):
        log.info('[email] no transport available - skipping reminder.')
        return 0

    outstanding = members_missing_picks(league, week)
    # Mark the week done either way. Nobody outstanding is a finished job, and
    # re-checking every tick for the rest of the window buys nothing.
    site_settings.reminder_sent_week = week
    site_settings.save(update_fields=['reminder_sent_week'])
    if not outstanding:
        log.info(f'[email] week {week}: everyone is in, no reminder needed.')
        return 0

    site_url = getattr(django_settings, 'SITE_URL', 'http://localhost:8000')
    picks_url = f'{site_url.rstrip("/")}/picks/'
    inbox = picks_address()

    lock_line = ''
    if site_settings.auto_lock_dt:
        left = _format_lock_delta(site_settings.auto_lock_dt)
        when = _format_lock_dt(site_settings.auto_lock_dt, site_settings.auto_tz)
        lock_line = f'Picks lock in {left} ({when}).\n'

    subject = f'Week {week} picks close soon'
    messages = []
    for user, made, total in outstanding:
        if made:
            standing = (f'You have {made} of {total} games in. The missing '
                        f'{total - made} still need a pick.\n\n'
                        f'A partial ballot scores nothing: if all of your picks '
                        f'are not in on time, none of them count.\n')
        else:
            standing = f'You have not picked any of this week\'s {total} games yet.\n'
        body = (f'{standing}\n{lock_line}'
                f'\nMake your picks: {picks_url}\n'
                f'\n\n{signoff(league)}')
        messages.append((user.email, body))

    # One feed entry for the batch, not one per member: the feed is the league's
    # record of what went out, and nineteen near-identical rows would bury it.
    record_site_email(
        league,
        subject=subject,
        body=(f'Reminder sent to {len(messages)} member(s) with incomplete picks '
              f'for week {week}.\n\n{lock_line}'),
        recipient_count=len(messages),
        slug=f'{league.slug}-reminder-w{week}',
    )

    def _send_each():
        sent = 0
        for address, body in messages:
            if smtp_ready():
                ok = send_via_mailbox(address, subject, body,
                                      reply_to=inbox or None)[0]
            else:
                ok = _send_via_resend(address, subject, body)
            sent += 1 if ok else 0
        log.info(f'[email] reminder sent to {sent}/{len(messages)} for week {week}')

    threading.Thread(target=_send_each, daemon=True).start()
    return len(messages)


def _send_via_resend(address, subject, body):
    """Single-recipient Resend send. Returns True on success."""
    api_key = getattr(django_settings, 'RESEND_API_KEY', '')
    if not api_key:
        return False
    try:
        import resend
        resend.api_key = api_key
        resend.Emails.send({
            'from': getattr(django_settings, 'RESEND_FROM', 'onboarding@resend.dev'),
            'to': [address],
            'subject': subject,
            'text': body,
        })
        return True
    except Exception as e:
        log.error(f'[email] resend failed for {address}: {e}')
        return False


def send_picks_published_email(site_settings):
    """Send weekly picks-live notification to all non-bot users with an email address."""
    api_key = getattr(django_settings, 'RESEND_API_KEY', '')
    log.info(f'[email] send_picks_published_email called, week={site_settings.week}')
    if not site_settings.email_picks_live:
        log.info('[email] picks-live email switched off on the Emails page.')
        return
    # Either transport will do; the mailbox is preferred further down.
    if outbound_suppressed() or (not api_key and not smtp_ready()):
        log.info('[email] no transport available - skipping.')
        return

    league = site_settings.league
    recipients = league_recipients(league, weekly=True)
    log.info(f'[email] {len(recipients)} recipient(s)')
    if not recipients:
        log.info('[email] No recipients - skipping.')
        return

    week = site_settings.week
    site_url = getattr(django_settings, 'SITE_URL', 'http://localhost:8000')
    from_email = getattr(django_settings, 'RESEND_FROM', 'onboarding@resend.dev')
    picks_url = f'{site_url.rstrip("/")}/picks/'

    # Replies go to the tagged picks address, so an edited ballot is unambiguously
    # a pick submission — including from someone whose plain mail is published,
    # where a reply would otherwise be broadcast to the league as an announcement.
    inbox = picks_address()

    from .models import Game
    games = list(Game.objects.filter(league=league, week=week))
    games.sort(key=lambda g: (g.game_dt is None, g.game_dt, g.id))
    ballot = build_ballot(games) if (inbox and site_settings.email_ballot) else ''
    if games and not inbox:
        log.info('[email] IMAP_USER not set - sending without a reply-by-email '
              'ballot, since replies would go nowhere')

    lock_line = ''
    if site_settings.auto_lock_dt:
        time_left = _format_lock_delta(site_settings.auto_lock_dt)
        lock_when = _format_lock_dt(site_settings.auto_lock_dt, site_settings.auto_tz)
        lock_line = f'Picks lock in {time_left} ({lock_when}). Get yours in before then.\n'
    elif site_settings.first_game_dt:
        lock_line = 'Picks lock before the first kickoff.\n'

    # Three sections, in this order: what the commissioner wrote, what happened
    # last week, and the ballot. The ballot sits last because it is the longest
    # part by far - one line per game - and anything after it is never read.
    intro_section = ''
    if site_settings.weekly_intro.strip():
        # Substituted here, not when the intro was chosen, so a template
        # written once stays correct every week it is reused. replace(),
        # never format(): the text is hand-edited and a stray brace must not
        # raise mid-send.
        intro_section = (site_settings.weekly_intro.strip()
                         .replace('{week}', str(week))
                         .replace('{league}', league.name) + '\n\n')

    # Week 1 has no previous week in this season, so it never carries a recap.
    # `weekly_recap` is a single field that persists across a season boundary,
    # so without this guard the opening email of a new season could lead with
    # last season's closing write-up under a "Last Week" heading.
    recap_section = ''
    has_previous_week = week > 1
    if site_settings.weekly_recap and site_settings.email_recap and has_previous_week:
        recap_section = (f'\n── Last Week ─────────────────────────────────\n\n'
                         f'{site_settings.weekly_recap}\n')

    # build_ballot() opens with its own "Reply with your picks" rule, so a
    # section header here stacked two dividers with nothing between them.
    ballot_section = f'\n{ballot}\n' if ballot else ''

    subject = f'Week {week} picks are live'
    body = (
        f'Week {week} picks are up.\n\n'
        f'{intro_section}'
        f'{lock_line}'
        f'\nMake your picks on the site: {picks_url}\n'
        f'{recap_section}'
        f'{ballot_section}'
        f'\n\n{signoff(league)}'
    )

    # Into the feed before the send thread starts: the page should show what the
    # league was told even if Resend then fails on every address.
    record_site_email(
        league,
        subject=subject, body=body, recipient_count=len(recipients),
        slug=f'{league.slug}-picks-live-w{week}',
    )

    # Deliberately per recipient rather than one post to the list, even though the
    # list would be one send: this mail carries the ballot, and a member hitting
    # reply on a group message could broadcast their picks to the whole league.
    # Sent individually, a reply can only go back to the mailbox.
    if smtp_ready():
        def _send_each():
            # Reply-To is the tagged picks address, so replying to the ballot
            # submits picks rather than being read as an announcement.
            sent = sum(1 for a in recipients
                       if send_via_mailbox(a, subject, body,
                                           reply_to=inbox or None)[0])
            log.info(f'[email] picks-live sent to {sent}/{len(recipients)} '
                  f'for week {week}')
        threading.Thread(target=_send_each, daemon=True).start()
        return

    def _send():
        # One message per recipient. Putting the whole league in a single `to`
        # would disclose every member's address to everyone else.
        try:
            import resend
            resend.api_key = api_key
        except Exception as e:
            log.error(f'[email] could not init resend: {e}')
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
                log.error(f'[email] send failed for one recipient: {e}')
        log.info(f'[email] sent OK to {sent}/{len(recipients)} recipients for week {week}')

    threading.Thread(target=_send, daemon=True).start()
