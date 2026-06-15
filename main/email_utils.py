import logging
import threading
from datetime import datetime, timezone

from django.core.mail import send_mail
from django.conf import settings as django_settings
from django.contrib.auth.models import User

log = logging.getLogger(__name__)


def _format_lock_delta(lock_dt):
    """Return human-readable time until lock_dt, e.g. '2 days 4 hours'."""
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
    """Return lock time as readable local string, e.g. 'Sunday at 11:00 AM ET'."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_str or 'UTC')
    except Exception:
        tz = timezone.utc
    local = lock_dt.astimezone(tz)
    tz_label = tz_str.replace('_', ' ').split('/')[-1] if tz_str else 'UTC'
    return local.strftime(f'%A at %-I:%M %p') + f' {tz_label}'


def send_picks_published_email(site_settings):
    """Send weekly picks-live notification to all non-bot users with an email address."""
    print(f'[email] send_picks_published_email called, week={site_settings.week}', flush=True)
    if not django_settings.EMAIL_HOST_USER:
        print('[email] EMAIL_HOST_USER not set — skipping.', flush=True)
        return

    recipients = list(
        User.objects.filter(email__isnull=False)
        .exclude(email='')
        .exclude(profile__is_bot=True)
        .values_list('email', flat=True)
    )
    print(f'[email] recipients: {recipients}', flush=True)
    if not recipients:
        print('[email] No recipients — skipping.', flush=True)
        return

    week = site_settings.week
    site_url = getattr(django_settings, 'SITE_URL', 'http://localhost:8000')
    picks_url = f'{site_url}/picks/'

    # Lock time info
    lock_line = ''
    if site_settings.auto_lock_dt:
        time_left = _format_lock_delta(site_settings.auto_lock_dt)
        lock_when = _format_lock_dt(site_settings.auto_lock_dt, site_settings.auto_tz)
        lock_line = f'Picks lock in {time_left} ({lock_when}). Get yours in before then.\n'
    elif site_settings.first_game_dt:
        lock_line = 'Picks lock before the first kickoff.\n'

    # Previous week recap
    recap_section = ''
    if site_settings.weekly_recap:
        recap_section = f'\n── Last Week ─────────────────────────────────\n\n{site_settings.weekly_recap}\n'

    subject = f'Week {week} picks are live'
    body = (
        f'Week {week} picks are up.\n\n'
        f'{lock_line}'
        f'\nMake your picks: {picks_url}'
        f'{recap_section}'
        f'\n\n──\nPutnamBowl'
    )

    def _send():
        try:
            print(f'[email] attempting send to {recipients}', flush=True)
            send_mail(
                subject=subject,
                message=body,
                from_email=django_settings.DEFAULT_FROM_EMAIL,
                recipient_list=recipients,
                fail_silently=False,
            )
            print(f'[email] sent OK to {len(recipients)} recipients for week {week}', flush=True)
        except Exception as e:
            print(f'[email] FAILED: {e}', flush=True)

    threading.Thread(target=_send, daemon=True).start()
