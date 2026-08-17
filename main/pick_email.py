"""Let members submit their picks by replying to an email.

Mail sent **directly** to the site's mailbox — rather than to the league list —
is treated as a pick submission. There is no Google Group involved, so the sender
does not need to be a group member; the mailbox is an ordinary inbox.

No extra permission gates this. Setting your own picks is something every member
can already do on the site, so an authenticated address is enough. Publishing to
the shared Emails feed is the privileged action, and that keeps its own flag.

## Parsing

Two passes, cheapest and most trustworthy first:

1. **Deterministic.** Find team names and abbreviations in the text and map them
   to this week's games. For the common shape — "KC, PHI, give me the Panthers" —
   this is exact, and being exact matters more than being clever: a wrong pick
   silently sabotages someone's week.
2. **Gemini**, only for games pass 1 could not resolve, and only when the text
   plausibly refers to them. If it fails or is unconfigured, those games are left
   unpicked and the reply asks about them.

Never guess. `choose_picks()` may fall back to random for PutnamBot because a bot
with no picks is worse than a bot with arbitrary ones; here the opposite holds.

## Reply

Every submission gets a reply listing exactly what was recorded and what was not.
For members who find the site awkward — the reason this exists — a silent
misparse would be worse than no feature at all.
"""
import logging
import re

from django.conf import settings as django_settings
from django.utils import timezone

from .models import Game, Pick, SiteSettings
from .teams import TEAM_ABBREV

log = logging.getLogger(__name__)

# Common shorthand the league actually uses, beyond full names and abbreviations.
NICKNAMES = {
    'niners': 'San Francisco 49ers', '49ers': 'San Francisco 49ers',
    'sf': 'San Francisco 49ers', 'frisco': 'San Francisco 49ers',
    'bucs': 'Tampa Bay Buccaneers', 'tampa': 'Tampa Bay Buccaneers',
    'jags': 'Jacksonville Jaguars', 'jax': 'Jacksonville Jaguars',
    'pats': 'New England Patriots', 'new england': 'New England Patriots',
    'philly': 'Philadelphia Eagles', 'kc': 'Kansas City Chiefs',
    'chiefs': 'Kansas City Chiefs', 'gb': 'Green Bay Packers',
    'packers': 'Green Bay Packers', 'pack': 'Green Bay Packers',
    'wsh': 'Washington Commanders', 'commies': 'Washington Commanders',
    'lions': 'Detroit Lions', 'vikes': 'Minnesota Vikings',
    'boys': 'Dallas Cowboys', 'skins': 'Washington Commanders',
    'no': 'New Orleans Saints', 'nola': 'New Orleans Saints',
    'lv': 'Las Vegas Raiders', 'vegas': 'Las Vegas Raiders',
    'la rams': 'Los Angeles Rams', 'la chargers': 'Los Angeles Chargers',
    'ny giants': 'New York Giants', 'ny jets': 'New York Jets',
}


# Abbreviations that are also ordinary English words. "NO" for New Orleans is the
# dangerous one: "no, give me the Chiefs" would otherwise register a Saints pick.
# These match only in upper case, which is how anyone writing them as a team does.
CASE_SENSITIVE_ALIASES = {'no', 'ne', 'in', 'on', 'at', 'it', 'so', 'or', 'as', 'a'}

# A mention is *against* that team when one of these sits immediately before it:
# either a beats-word, so the team named after it is the loser ("Chargers over
# Denver"), or a negation ("not the Bills", "anyone but the Jets").
#
# Without this, naming a team as the loser picked it. "Chargers over Denver"
# selected Denver, and "not taking the Bills" selected Buffalo — the exact silent
# wrong pick this module exists to avoid.
_THE = r"\s+(?:the\s+)?$"
AGAINST_BEFORE = re.compile(
    # The team after a beats-word is the loser: "Chargers over Denver".
    r"(?:(?:\bover\b|\bbeats?\b|\bbeating\b|\bdef\b|\bdefeats?\b|\btops\b|>"
    r"|\bto beat\b)" + _THE + r")"
    # Negated choice verbs: "not taking the Bills", "don't want Denver".
    r"|(?:(?:\bnot\b|\bdon'?t\b|\bdo not\b|\bnever\b)\s+"
    r"(?:taking|take|going with|going|want|like|picking|pick|backing|back|trust)"
    + _THE + r")"
    # Standalone against-words, which must sit right before the team.
    r"|(?:(?:\bavoid\b|\bfade\b|\bagainst\b|\banyone but\b|\bother than\b"
    r"|\bnot\b|\bno\b)" + _THE + r")",
    re.IGNORECASE,
)
# How far back to look. Short, so a beats-word from an earlier clause cannot
# reach across and flip an unrelated team.
AGAINST_WINDOW = 22


def _unique_parts():
    """City and mascot fragments that identify exactly one team.

    People write "give me Philadelphia" or "Green Bay" as often as they write the
    mascot, so the city has to count. But two teams share New York and two share
    Los Angeles, so those are left out rather than resolved arbitrarily — derived
    from the team list instead of hardcoded, so it stays true if a team moves.
    """
    cities, mascots = {}, {}
    for team in TEAM_ABBREV:
        head, _, tail = team.rpartition(' ')
        cities.setdefault(head.lower(), []).append(team)
        mascots.setdefault(tail.lower(), []).append(team)
    unique = {}
    for bucket in (cities, mascots):
        for part, teams in bucket.items():
            if len(teams) == 1 and part:
                unique[part] = teams[0]
    return unique


_UNIQUE_PARTS = _unique_parts()


def _aliases_for(team):
    """Every string that unambiguously means this team."""
    out = {team.lower(), TEAM_ABBREV.get(team, '').lower()}
    out |= {part for part, owner in _UNIQUE_PARTS.items() if owner == team}
    for alias, full in NICKNAMES.items():
        if full == team:
            out.add(alias)
    return {a for a in out if a}


def _other(side):
    return 'team2' if side == 'team1' else 'team1'


def _find_mentions(text, games):
    """Map each game to the sides mentioned, each marked for or against.

    Returns {game_id: [(position, side, is_for), ...]} sorted by position.
    """
    hits = {}
    for game in games:
        for side, team in (('team1', game.team1), ('team2', game.team2)):
            for alias in _aliases_for(team):
                # Bounded by non-alphanumerics so "NO" does not match "notice"
                # and "LA" does not match "later".
                cased = alias in CASE_SENSITIVE_ALIASES
                target = alias.upper() if cased else alias
                pattern = rf'(?<![A-Za-z0-9]){re.escape(target)}(?![A-Za-z0-9])'
                for m in re.finditer(pattern, text, 0 if cased else re.IGNORECASE):
                    before = text[max(0, m.start() - AGAINST_WINDOW):m.start()]
                    is_for = not AGAINST_BEFORE.search(before)
                    hits.setdefault(game.id, []).append((m.start(), side, is_for))
    for gid in hits:
        hits[gid].sort()
    return hits


def parse_deterministic(text, games):
    """Picks we can read off the text with certainty.

    A pick'em game is a binary choice, so naming a team as the loser decides it
    just as well as naming the winner: "Chargers over Denver" picks the Chargers
    in their game and the Broncos' opponent in theirs.

    Resolution per game:

    * one side spoken for, the other not  -> that side
    * nobody for, exactly one side against -> the opponent
    * both sides for, or both against, or contradictory -> deferred, not guessed

    Hedged prose can still be misread — "not sure about KC" reads as a KC pick,
    because the negation attaches to the writer's confidence rather than to the
    team. Gemini gets a second look at anything left unresolved, and the
    confirmation reply is the real backstop: the sender always sees what was
    recorded and can correct it.
    """
    picks, ambiguous = {}, []
    by_id = {g.id: g for g in games}

    for gid, found in _find_mentions(text, games).items():
        favoured = {side for _, side, is_for in found if is_for}
        opposed = {side for _, side, is_for in found if not is_for}

        if len(favoured) == 1:
            side = favoured.pop()
            # "Chargers over KC": the Chargers are for, KC against — consistent.
            if side not in opposed:
                picks[gid] = side
                continue
        elif not favoured and len(opposed) == 1:
            picks[gid] = _other(opposed.pop())
            continue
        ambiguous.append(by_id[gid])
    return picks, ambiguous


def _gemini_fill(text, games):
    """Ask Gemini about games the deterministic pass could not settle."""
    if not games:
        return {}
    try:
        api_key = getattr(django_settings, 'GEMINI_API_KEY', '')
        if not api_key:
            log.info('[pick_email] GEMINI_API_KEY not set — leaving %s game(s) unpicked',
                     len(games))
            return {}
        from google import genai
        from .ai_picks import MODEL, _parse
        client = genai.Client(api_key=api_key)
    except Exception as e:
        log.error('[pick_email] could not build Gemini client: %s', e)
        return {}

    listing = '\n'.join(
        f'- id {g.id}: {g.team1} vs {g.team2}' for g in games
    )
    prompt = f"""A member of an NFL pick'em league emailed the message below with
their picks. Work out which team they chose in each game listed.

Only answer for games where their intent is clear. If the message does not say
who they want in a game, or is ambiguous, leave that game out entirely. Do not
guess — a wrong pick is worse than a missing one.

Answer "team1" for the first team listed in a game, "team2" for the second.

Games still undecided:
{listing}

Their message:
\"\"\"
{text[:4000]}
\"\"\"

Respond with ONLY a JSON object mapping game id (as a string) to "team1" or
"team2", omitting any game you are unsure about. No markdown, no commentary."""

    try:
        response = client.models.generate_content(model=MODEL, contents=prompt)
        return _parse(response.text, {g.id for g in games})
    except Exception as e:
        log.error('[pick_email] Gemini call failed: %s', e)
        return {}


def parse_picks(text, games):
    """Return ({game_id: choice}, [unresolved games])."""
    picks, ambiguous = parse_deterministic(text, games)
    unresolved = [g for g in games if g.id not in picks]
    if unresolved:
        for gid, choice in _gemini_fill(text, unresolved).items():
            picks[gid] = choice
    return picks, [g for g in games if g.id not in picks]


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
        lines.append(f"I could not work out any picks from your message, so nothing "
                     f"has been saved for Week {settings.week}.")
        lines.append('')

    if unresolved:
        lines.append(f'I could not tell who you wanted in '
                     f'{"these" if len(unresolved) > 1 else "this"}:')
        lines.append('')
        for game in unresolved:
            lines.append(f'  {game.team1} vs {game.team2}')
        lines.append('')
        lines.append('Reply with just those and I will add them.')
        lines.append('')

    total = len(games)
    lines.append(f'That is {len(saved)} of {total} game{"s" if total != 1 else ""}.')
    if settings.auto_lock_dt:
        lines.append(f'Picks lock at {settings.auto_lock_dt:%A %d %B, %H:%M} UTC.')
    lines.append('')
    lines.append('You can also change any of these on the site.')
    lines.append('')
    lines.append('—')
    lines.append("PutnamBot, reading the league's mail")
    return '\n'.join(lines)


def send_reply(to_email, subject, body):
    """Reply to the sender only. Never to the list — these are private picks."""
    api_key = getattr(django_settings, 'RESEND_API_KEY', '')
    if not api_key:
        print(f'[pick_email] RESEND_API_KEY not set — reply to {to_email} not sent',
              flush=True)
        return False
    from_email = getattr(django_settings, 'RESEND_FROM', 'onboarding@resend.dev')
    try:
        import resend
        resend.api_key = api_key
        resend.Emails.send({
            'from': from_email,
            'to': [to_email],
            'subject': subject,
            'text': body,
        })
        print(f'[pick_email] replied to {to_email}', flush=True)
        return True
    except Exception as e:
        log.error('[pick_email] reply to %s failed: %s', to_email, e)
        print(f'[pick_email] reply to {to_email} FAILED: {e}', flush=True)
        return False


def handle(user, text, reply_to=None):
    """Parse and save picks from one email. Returns a human-readable outcome."""
    settings = SiteSettings.get()
    reply_to = reply_to or user.email
    week = settings.week

    def _reply(body, subject=None):
        if reply_to:
            send_reply(reply_to, subject or f'Your Week {week} picks', body)

    if not settings.publish:
        _reply(f"Week {week} isn't open for picks yet — the commissioner is still "
               f"setting up the games. Nothing has been saved. Send these again "
               f"once picks are live and I'll record them.")
        return 'week not published — nothing saved, sender told'

    if settings.lock_picks:
        _reply(f'Week {week} picks are locked, so I could not record these. '
               f"Sorry — they came in too late. Nothing has been changed.")
        return 'picks locked — nothing saved, sender told'

    games = list(Game.objects.filter(week=week))
    games.sort(key=lambda g: (g.game_dt is None, g.game_dt, g.id))
    if not games:
        _reply(f'There are no games scheduled for Week {week} yet, so I could not '
               f'record any picks.')
        return 'no games this week — nothing saved, sender told'

    picks, unresolved = parse_picks(text, games)

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
            f'{len(unresolved)} unresolved, sender told')
