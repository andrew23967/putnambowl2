"""Let members submit their picks by replying to an email.

Mail sent **directly** to the site's mailbox — rather than to the league list — is
treated as a pick submission. There is no Google Group involved, so the sender
does not need to be a group member; the mailbox is an ordinary inbox.

No extra permission gates this. Setting your own picks is something every member
can already do on the site, so an authenticated address is enough. Publishing to
the shared Emails feed is the privileged action, and that keeps its own flag.

## Reading the message

One Gemini call, and no hand-rolled parsing.

An earlier version matched team names with regexes and alias tables. It was about
two hundred lines and had three separate bugs: it picked the team named as the
*loser* ("Chargers over Denver" chose Denver), it read the English word "no" as
New Orleans, and it ignored city-only phrasing like "give me Philadelphia". Each
fix added more special cases. Understanding "not taking the Bills" is what a
language model is for.

What is **not** delegated is trusting the answer. The model's reply is validated
against the actual slate — only game ids from this week, only "team1" or "team2" —
so a hallucinated team or an invented id cannot become a pick.

Unlike `ai_picks.choose_picks()`, which falls back to random because a bot with no
picks is worse than a bot with arbitrary ones, here the opposite holds: a wrong
pick silently sabotages someone's week. Anything the model does not answer for is
left unpicked and asked about in the reply.

## Reply

Every submission gets a reply listing exactly what was recorded and what was not.
For members who find the site awkward — the reason this exists — a silent misparse
would be worse than no feature, so the receipt is the safety net. Don't remove it.
"""
import logging

from django.conf import settings as django_settings

from .models import Game, Pick, LeagueSettings

log = logging.getLogger(__name__)


PROMPT = """A member of a private NFL pick'em league emailed the message below to
submit their picks. Work out which team they chose in each game.

How they may have written it:

* The site mails them a list, one game per line, and they delete the team they do
  not want — so a line with a single team means that team is their pick. A line
  with both teams still on it means they have not chosen that game.
* Or in prose: "I'll take the Chiefs", "give me Philadelphia", "Chargers over
  Denver" (the Chargers win; Denver loses, so in Denver's game they want Denver's
  opponent), "not taking the Bills" (so their opponent).
* The message may include quoted text from earlier emails, below a line like
  "On ... wrote:". If the same game is answered more than once, the version
  highest up the message is the most recent and wins.

Rules:

* Only answer for games where their intent is genuinely clear.
* If they did not choose a game, or you are unsure, LEAVE IT OUT. A wrong pick is
  much worse than a missing one — never guess, and never fill in a game just to
  complete the set.
* "team1" means the first team listed for that game below, "team2" the second.

This week's games:
{listing}

Their message:
\"\"\"
{message}
\"\"\"

Respond with ONLY a JSON object mapping game id (as a string) to "team1" or
"team2", omitting every game you are not sure about. No markdown, no commentary.

Example, for a week whose game ids are 41, 42 and 43 where they answered two:
{{"41": "team2", "43": "team1"}}"""


def _ask_model(text, games):
    """Return the model's raw reply, or None if Gemini is unavailable.

    Split out from `extract_picks` so tests can stub the model and still exercise
    the validation and reporting around it.
    """
    api_key = getattr(django_settings, 'GEMINI_API_KEY', '')
    if not api_key:
        log.warning('[pick_email] GEMINI_API_KEY not set — cannot read picks')
        return None
    try:
        from google import genai
        from .ai_picks import model_name
        client = genai.Client(api_key=api_key)
    except Exception as e:
        log.error('[pick_email] could not build Gemini client: %s', e)
        return None

    listing = '\n'.join(
        f'- id {g.id}: {g.team1} (favourite, {g.points1} pts) '
        f'vs {g.team2} (underdog, {g.points2} pts)'
        for g in games
    )
    prompt = PROMPT.format(listing=listing, message=text[:8000])
    try:
        return client.models.generate_content(model=model_name(), contents=prompt).text
    except Exception as e:
        log.error('[pick_email] Gemini call failed: %s', e)
        return None


def extract_picks(text, games):
    """Return ({game_id: choice}, unresolved_games, model_available).

    `model_available` is False when Gemini could not be reached at all, which is a
    different thing to the model reading the message and finding nothing — the
    sender is told something different in each case.
    """
    if not games:
        return {}, [], True

    reply = _ask_model(text, games)
    if reply is None:
        return {}, list(games), False

    # Reuse ai_picks' tolerant JSON reader: it strips code fences, digs the object
    # out of surrounding prose, and drops anything not a real game id for this
    # week or not one of the two valid choices.
    from .ai_picks import _parse
    picks = _parse(reply, {g.id for g in games})
    log.info('[pick_email] model resolved %s/%s games', len(picks), len(games))
    return picks, [g for g in games if g.id not in picks], True


def _describe(game, choice):
    picked = game.team1 if choice == 'team1' else game.team2
    against = game.team2 if choice == 'team1' else game.team1
    points = game.points1 if choice == 'team1' else game.points2
    tag = ' (underdog)' if choice == 'team2' else ''
    return f'{picked} over {against} — {points} pts{tag}'


def build_reply(user, settings, saved, unresolved, games):
    """Compose the confirmation. Says what was recorded, in full."""
    name = user.profile.real_name or user.username
    lines = [f'Hi {name},', '']

    if saved:
        lines.append(f'Your Week {settings.week} picks are in. Here is what I recorded:')
        lines.append('')
        for game in games:
            if game.id in saved:
                lines.append(f'  {_describe(game, saved[game.id])}')
        lines.append('')
    else:
        lines.append(f'I could not work out any picks from your message, so nothing '
                     f'has been saved for Week {settings.week}.')
        lines.append('')

    if unresolved:
        lines.append(f'I could not tell who you wanted in '
                     f'{"these" if len(unresolved) > 1 else "this"}:')
        lines.append('')
        for game in unresolved:
            lines.append(f'  {game.team1}  /  {game.team2}')
        lines.append('')
        lines.append('Reply with just those — delete the team you do not want from '
                     'each line — and I will add them.')
        lines.append('')

    total = len(games)
    lines.append(f'That is {len(saved)} of {total} game{"s" if total != 1 else ""}.')
    if settings.auto_lock_dt:
        lines.append(f'Picks lock at {settings.auto_lock_dt:%A %d %B, %H:%M} UTC.')
    lines.append('')
    lines.append('You can also change any of these on the site.')
    lines.append('')
    lines.append('──')
    lines.append(settings.league.name)
    return '\n'.join(lines)


def send_reply(to_email, subject, body, in_reply_to=None, settings=None):
    """Reply to the sender only. Never to the list — these are private picks.

    Sent from the league mailbox when SMTP is configured, which is both simpler
    and necessary: Resend's sandbox sender only reaches the account owner until a
    domain is verified, so a confirmation could not have reached any member. From
    the mailbox it is a genuine reply — same address they wrote to, threaded — so
    it lands in the conversation they started.
    """
    from .email_utils import (outbound_suppressed, picks_address,
                              send_via_mailbox, smtp_ready)

    if settings is None:
        raise TypeError('send_reply needs the league settings')
    if not settings.email_confirmations:
        log.info('[pick_email] confirmations switched off - %s not told what was recorded',
                 to_email)
        return False

    if smtp_ready():
        # Corrections come back to the tagged address, so a follow-up is read as
        # picks and never as something to publish.
        ok, _ = send_via_mailbox(to_email, subject, body, in_reply_to=in_reply_to,
                                 reply_to=picks_address() or None)
        if ok:
            return True
        # Fall through to Resend rather than losing the confirmation entirely.

    api_key = getattr(django_settings, 'RESEND_API_KEY', '')
    if outbound_suppressed():
        log.info('[pick_email] outbound suppressed - reply to %s not sent', to_email)
        return False
    if not api_key:
        log.warning('[pick_email] no SMTP and no RESEND_API_KEY - reply to %s not sent',
                    to_email)
        return False
    from_email = getattr(django_settings, 'RESEND_FROM', 'onboarding@resend.dev')
    inbox = picks_address() or ''
    try:
        import resend
        resend.api_key = api_key
        payload = {
            'from': from_email,
            'to': [to_email],
            'subject': subject,
            'text': body,
        }
        # So a correction comes back to the mailbox we poll, not to RESEND_FROM.
        if inbox:
            payload['reply_to'] = [inbox]
        resend.Emails.send(payload)
        log.info('[pick_email] replied to %s via Resend', to_email)
        return True
    except Exception as e:
        log.error('[pick_email] reply to %s failed: %s', to_email, e)
        return False


def handle(user, text, reply_to=None, message_id=None, subject=None,
           notify_unavailable=True):
    """Parse and save picks from one email.

    Returns ``(outcome, retryable)``. ``retryable`` is True only when the model
    could not be reached — the caller then leaves the message unprocessed so a
    later poll tries again, rather than dropping the submission over a passing
    503. Set `notify_unavailable` False while retrying so the sender is not told
    about an outage that may clear on its own.

    `message_id` and `subject` come from the incoming mail so the confirmation
    threads as a reply to it, rather than arriving as an unrelated message — which
    matters most for the members this exists for.
    """
    settings = LeagueSettings.for_league(user.profile.league)
    reply_to = reply_to or user.email
    week = settings.week

    if subject:
        base = subject if subject.lower().startswith('re:') else f'Re: {subject}'
    else:
        base = f'Your Week {week} picks'

    def _reply(body, subject_override=None):
        if reply_to:
            send_reply(reply_to, subject_override or base, body,
                       in_reply_to=message_id, settings=settings)

    if not settings.publish:
        _reply(f"Week {week} isn't open for picks yet — the commissioner is still "
               f'setting up the games. Nothing has been saved. Send these again '
               f"once picks are live and I'll record them.")
        return 'week not published — nothing saved, sender told', False

    if settings.lock_picks:
        _reply(f'Week {week} picks are locked, so I could not record these. '
               f'Sorry — they came in too late. Nothing has been changed.')
        return 'picks locked — nothing saved, sender told', False

    games = list(Game.objects.filter(league=settings.league, week=week))
    games.sort(key=lambda g: (g.game_dt is None, g.game_dt, g.id))
    if not games:
        _reply(f'There are no games scheduled for Week {week} yet, so I could not '
               f'record any picks.')
        return 'no games this week — nothing saved, sender told', False

    picks, unresolved, available = extract_picks(text, games)

    if not available:
        # Retryable: the model was unreachable, which usually passes. The caller
        # leaves the message unprocessed so a later poll has another go, and only
        # tells the sender once we have given up.
        if notify_unavailable:
            _reply(f'I could not read your picks — the service I use to understand '
                   f'emails has been unavailable for a while, so nothing has been '
                   f'saved for Week {week}. Please make them on the site, or reply '
                   f'again later.')
            return 'model unavailable — gave up, sender told', False
        return 'model unavailable — will retry', True

    saved = {}
    for game in games:
        if game.id in picks:
            Pick.objects.update_or_create(
                user=user, game=game, defaults={'choice': picks[game.id]}
            )
            saved[game.id] = picks[game.id]

    _reply(build_reply(user, settings, saved, unresolved, games))
    log.info('[pick_email] %s: saved %s/%s for week %s',
             user.username, len(saved), len(games), week)
    return (f'picks from {user.username}: saved {len(saved)}/{len(games)}, '
            f'{len(unresolved)} unresolved, sender told'), False
