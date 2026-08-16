"""Ask Gemini to pick winners for a week's games.

Used by the `putnambot` account, whose whole gimmick is that a language model
makes its picks. It is a novelty entry in the league, not a serious predictor.

Everything here is best-effort by design. This runs inside the auto-pilot
worker, which must keep the season moving no matter what a third party does, so
any failure — missing key, network error, malformed response, a team name the
model invented — degrades to "no opinion" for the affected games. The caller
fills those in randomly rather than leaving the bot without picks.
"""
import json
import logging
import re

log = logging.getLogger(__name__)

MODEL = 'gemini-3.5-flash'


def _client():
    """Return a configured Gemini client, or None if unavailable."""
    try:
        from django.conf import settings as django_settings
        api_key = getattr(django_settings, 'GEMINI_API_KEY', '')
        if not api_key:
            log.info('[ai_picks] GEMINI_API_KEY not set — falling back to random.')
            return None
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception as e:
        log.error('[ai_picks] could not build Gemini client: %s', e)
        return None


def _build_prompt(games):
    lines = []
    for g in games:
        # team1 is the favorite (worth 1.0x), team2 the underdog (worth more).
        lines.append(
            f'- id {g.id}: {g.team1} (favorite, {g.points1} pts) '
            f'vs {g.team2} (underdog, {g.points2} pts)'
        )
    listing = '\n'.join(lines)
    return f"""You are PutnamBot, an entry in a private NFL pick'em league.

Pick the winner of each game below. For each one answer "team1" for the
favorite or "team2" for the underdog. Underdogs are worth more points, so take
an upset when you genuinely think it is live — but do not pick upsets at random.

Games:
{listing}

Respond with ONLY a JSON object mapping each game id (as a string) to "team1"
or "team2". No markdown, no code fences, no commentary.

Example for two games with ids 7 and 8:
{{"7": "team1", "8": "team2"}}"""


def _parse(text, valid_ids):
    """Pull a {game_id: choice} mapping out of the model's reply."""
    if not text:
        return {}
    cleaned = text.strip()
    # Strip ```json fences if the model adds them despite instructions.
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```[a-zA-Z]*\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
    # Fall back to the first {...} block if there is surrounding prose.
    if not cleaned.startswith('{'):
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        cleaned = match.group(0) if match else ''

    try:
        raw = json.loads(cleaned)
    except (ValueError, TypeError) as e:
        log.error('[ai_picks] could not parse response: %s', e)
        return {}
    if not isinstance(raw, dict):
        return {}

    picks = {}
    for key, value in raw.items():
        try:
            gid = int(key)
        except (TypeError, ValueError):
            continue
        if gid in valid_ids and value in ('team1', 'team2'):
            picks[gid] = value
    return picks


def choose_picks(games):
    """Return {game_id: 'team1'|'team2'} for as many games as Gemini answered.

    Games the model skipped or got wrong are simply absent from the result;
    the caller decides what to do about them.
    """
    games = list(games)
    if not games:
        return {}

    client = _client()
    if client is None:
        return {}

    try:
        response = client.models.generate_content(
            model=MODEL, contents=_build_prompt(games)
        )
        picks = _parse(response.text, {g.id for g in games})
    except Exception as e:
        log.error('[ai_picks] Gemini call failed: %s', e)
        return {}

    log.info('[ai_picks] Gemini returned %s/%s picks', len(picks), len(games))
    return picks
