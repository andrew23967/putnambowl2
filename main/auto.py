import logging
from datetime import datetime, timezone, timedelta

from django.contrib.auth.models import User
from django.db.models import Q, Prefetch

from .models import SiteSettings, Game, Pick, WeeklyLeaderboard
from .teams import ABBREV_TO_TEAM, TEAM_ABBREV
from . import scrape as scrape_module

log = logging.getLogger(__name__)

WEEKDAY_NAMES = {0: 'Monday', 1: 'Tuesday', 2: 'Wednesday', 3: 'Thursday',
                 4: 'Friday', 5: 'Saturday', 6: 'Sunday'}


def _calculate_points(underdog_ml, favorite_ml):
    u = abs(float(underdog_ml))
    f = abs(float(favorite_ml))
    if u == 0 or f == 0:
        return 1.0
    u_ratio = u / 100
    f_ratio = 100 / f
    hp = ((1 / (u_ratio * f_ratio)) ** 0.5) - 1
    return round((hp + 1) * u_ratio, 2)


def _current_season_year():
    from datetime import date
    today = date.today()
    return today.year if today.month >= 9 else today.year - 1


def _next_weekday_hour(weekday, hour, minute=0):
    """Return the next FUTURE UTC datetime for the given weekday/hour/minute."""
    now = datetime.now(timezone.utc)
    days_ahead = weekday - now.weekday()
    if days_ahead < 0 or (days_ahead == 0 and (now.hour, now.minute) >= (hour, minute)):
        days_ahead += 7
    return (now + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)


def _this_or_next_weekday_hour(weekday, hour, minute=0):
    """Like _next_weekday_hour but if the time already passed today, returns today's
    past time so auto_tick fires it immediately on the next check."""
    now = datetime.now(timezone.utc)
    days_ahead = weekday - now.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return (now + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)


def build_recap(week):
    """Generate recap text for the given week using Gemini. Falls back to template if unavailable."""
    games = list(Game.objects.filter(week=week).prefetch_related(
        Prefetch('picks', queryset=Pick.objects.select_related('user'))
    ))
    if not games:
        return None

    player_scores = {}
    game_lines = []
    for g in games:
        t1, t2 = g.team1, g.team2
        p1, p2 = g.points1, g.points2
        winner = t1 if g.winner == 'team1' else (t2 if g.winner == 'team2' else 'Tie')
        pick_strs = []
        for pick in g.picks.all():
            pts = pick.points_earned if pick.is_correct else 0
            player_scores[pick.user.username] = round(
                player_scores.get(pick.user.username, 0) + pts, 1
            )
            pick_strs.append(f"{pick.user.username}→{pick.choice or 'no pick'}")
        game_lines.append(f'{t1} ({p1}pts) vs {t2} ({p2}pts) — winner: {winner} | picks: {", ".join(pick_strs)}')

    ranked = sorted(player_scores.items(), key=lambda x: -x[1])
    if not ranked:
        return None

    league_avg = round(sum(s for _, s in ranked) / len(ranked), 1)
    standings_str = '\n'.join(f'{i+1}. {name}: {pts} pts' for i, (name, pts) in enumerate(ranked))
    games_str = '\n'.join(game_lines)

    prompt = f"""You are the commissioner of a private NFL pick'em fantasy league called PutnamBowl.
Write a factual weekly recap for Week {week} in 3 short paragraphs. Report what happened: who won, who lost, the scores, and how people's picks went. Straightforward and informative — no jokes, no sarcasm, no filler.

Week {week} results:

Standings (points earned this week):
{standings_str}

League average: {league_avg} pts

Games:
{games_str}

Write the recap now. Plain text only, no markdown, no headers."""

    try:
        from django.conf import settings as django_settings
        api_key = getattr(django_settings, 'GEMINI_API_KEY', '')
        if api_key:
            from google import genai
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=prompt,
            )
            recap = response.text.strip()
            log.info('Gemini recap generated for week %s', week)
            return recap
    except Exception as e:
        log.error('Gemini recap failed: %s', e)
        print(f'[recap] Gemini failed: {e}', flush=True)

    # Fallback to template-based recap
    winner_name, winner_pts = ranked[0]
    last_place_name, last_place_pts = ranked[-1]
    second_name = ranked[1][0] if len(ranked) > 1 else None
    second_pts = ranked[1][1] if len(ranked) > 1 else 0

    p1 = (f"Week {week} is in the books. {winner_name} took the week with {winner_pts} points"
          + (f", edging out {second_name} who finished with {second_pts}" if second_name else "")
          + f". League average was {league_avg} points.")
    p2 = f"{last_place_name} finished last with {last_place_pts} points. Better luck next week."
    return f"{p1}\n\n{p2}"


def build_intro():
    """Generate a PutnamBot season intro. Falls back to a static message if Gemini unavailable."""
    prompt = """You are PutnamBot, the AI commissioner of a private NFL pick'em fantasy league called PutnamBowl.
Write a short introduction (2-3 sentences) to kick off the new season. Introduce yourself by name, explain that you will be managing the league, sending weekly emails when picks are published, and writing weekly recaps after each week's games. Keep it straightforward and professional."""

    try:
        from django.conf import settings as django_settings
        api_key = getattr(django_settings, 'GEMINI_API_KEY', '')
        if api_key:
            from google import genai
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=prompt,
            )
            return response.text.strip()
    except Exception as e:
        log.error('PutnamBot intro failed: %s', e)
        print(f'[recap] PutnamBot intro failed: {e}', flush=True)

    return ("Welcome to PutnamBowl. I'm PutnamBot, your AI league commissioner. "
            "Going forward I'll manage the league, send emails when weekly picks are published, "
            "and post a recap here after each week's games are complete.")


def make_bot_picks(week=None):
    """Create picks for all bot users based on their underdog percentage.

    Scoped to a single week — picking across every game ever would retroactively
    add bot picks to completed weeks and rewrite league history.
    """
    import random as _random
    week = SiteSettings.get().week if week is None else week
    bots = list(User.objects.select_related('profile').filter(profile__is_bot=True))
    games = list(Game.objects.filter(week=week))
    if not bots or not games:
        return

    bot_ids = [b.id for b in bots]
    existing = set(
        Pick.objects.filter(user_id__in=bot_ids, game__week=week)
        .values_list('user_id', 'game_id')
    )

    # Gemini is only consulted if an AI bot actually needs picks this week, and
    # only once for the whole slate rather than once per game.
    ai_picks = {}
    ai_bots = [b for b in bots if b.profile.bot_strategy == 'gemini']
    ai_needs = [g for g in games
                if any((b.id, g.id) not in existing for b in ai_bots)]
    if ai_bots and ai_needs:
        from .ai_picks import choose_picks
        try:
            ai_picks = choose_picks(ai_needs)
        except Exception as e:
            # Never let the picker stall the season.
            log.error('AI picks failed, falling back to random: %s', e)
            ai_picks = {}

    new_picks = []
    for bot in bots:
        pct = bot.profile.bot_underdog_pct
        use_ai = bot.profile.bot_strategy == 'gemini'
        for game in games:
            if (bot.id, game.id) in existing:
                continue
            choice = ai_picks.get(game.id) if use_ai else None
            if choice is None:
                choice = 'team2' if _random.randint(1, 100) <= pct else 'team1'
            new_picks.append(Pick(user=bot, game=game, choice=choice))
    Pick.objects.bulk_create(new_picks, batch_size=500)
    log.info('Bot picks: %s created for %s bots (%s AI) across %s games in week %s',
             len(new_picks), len(bots), len(ai_bots), len(games), week)


def _game_day_in_filter(game_dt, from_day, to_day, tz_str='UTC'):
    if from_day is None or to_day is None:
        return True
    if game_dt is None:
        return True
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_str or 'UTC')
    except Exception:
        tz = timezone.utc
    day = game_dt.astimezone(tz).weekday()
    if from_day <= to_day:
        return from_day <= day <= to_day
    return day >= from_day or day <= to_day


def do_scrape_and_publish(settings, year=None):
    year = year or _current_season_year()

    week_type = scrape_module.get_week_type(settings.week, year)
    auto_multiplier = {'regular': 1, 'playoffs': 2, 'superbowl': 4}[week_type]
    if settings.multiplier != auto_multiplier:
        log.info('Auto: multiplier → %sx (%s, week %s)', auto_multiplier, week_type, settings.week)
        settings.multiplier = auto_multiplier

    games_data = scrape_module.scrape(week=settings.week, api_type=settings.scrape_api, year=year)
    from_day = settings.scrape_filter_from_day
    to_day = settings.scrape_filter_to_day
    added = 0
    for g in games_data:
        game_dt = g[6]
        if not _game_day_in_filter(game_dt, from_day, to_day, settings.auto_tz):
            continue
        team1 = ABBREV_TO_TEAM.get(g[0], g[0])
        team2 = ABBREV_TO_TEAM.get(g[1], g[1])
        game_id = g[5]
        # Scope to this week — games persist across weeks now, and division
        # rivals play the same matchup twice a season.
        if Game.objects.filter(week=settings.week).filter(
            Q(game_id=game_id) | Q(team1=team1, team2=team2)
        ).exists():
            continue
        ug_ml, fav_ml = g[2], g[3]
        pts2 = (_calculate_points(ug_ml, abs(fav_ml)) * settings.multiplier
                if ug_ml and fav_ml else float(settings.multiplier))
        Game.objects.create(
            team1=team1, team2=team2,
            points1=float(settings.multiplier), points2=pts2,
            home_team=g[4], game_id=game_id, game_dt=game_dt,
            week=settings.week,
        )
        added += 1

    first_dt = scrape_module.get_first_game_dt(week=settings.week, year=year)
    settings.first_game_dt = first_dt
    if settings.lock_mode == 'offset' and first_dt and settings.auto_lock_offset_minutes:
        settings.auto_lock_dt = first_dt - timedelta(minutes=settings.auto_lock_offset_minutes)
    settings.publish = True
    settings.edit = False
    settings.save()
    log.info('Auto scrape+publish: week %s, %s games added, first kickoff %s', settings.week, added, first_dt)
    make_bot_picks(week=settings.week)

    try:
        from .email_utils import send_picks_published_email
        print('[auto] calling send_picks_published_email', flush=True)
        send_picks_published_email(settings)
    except Exception as e:
        print(f'[auto] email error: {e}', flush=True)
        log.error('Email send failed: %s', e)

    return added


def do_lock_picks(settings):
    settings.lock_picks = True
    settings.save()
    log.info('Auto: picks locked for week %s', settings.week)


def do_grade(settings, year=None, week=None):
    year = year or _current_season_year()
    week = settings.week if week is None else week
    results = scrape_module.grade(week=week, api_type=settings.grade_api, year=year)
    graded = 0
    for game in Game.objects.filter(week=week, graded=False):
        for r in results:
            game_id, outcome, home_abbrev, away_abbrev = r[0], r[1], r[2], r[3]
            # Primary: game_id match
            matched = bool(game.game_id and game.game_id == game_id)
            # Fallback: match by team abbreviations in case IDs differ across APIs
            if not matched:
                g_home = TEAM_ABBREV.get(game.team2 if game.home_team else game.team1, '').upper()
                g_away = TEAM_ABBREV.get(game.team1 if game.home_team else game.team2, '').upper()
                matched = g_home == home_abbrev.upper() and g_away == away_abbrev.upper()
            if not matched:
                continue
            if outcome == 'home':
                game.winner = 'team2' if game.home_team else 'team1'
            elif outcome == 'away':
                game.winner = 'team1' if game.home_team else 'team2'
            else:
                game.winner = 'tie'
            game.graded = True
            game.save()
            graded += 1
            break
    if graded:
        log.info('Auto: graded %s game(s) for week %s', graded, week)
    return graded


def do_advance_week(settings):
    games = list(Game.objects.filter(week=settings.week))
    players = list(User.objects.select_related('profile').all())
    all_picks = {(p.user_id, p.game_id): p for p in Pick.objects.filter(game__in=games)}

    lb_entries = [{'username': p.username, 'score': round(p.profile.score, 1)} for p in players]
    WeeklyLeaderboard.objects.update_or_create(week=settings.week, defaults={'entries': lb_entries})

    max_score = 0
    for g in games:
        for p in players:
            pick = all_picks.get((p.id, g.id))
            if pick and pick.is_correct:
                p.profile.score += pick.points_earned
        if g.winner == 'team1':
            max_score += g.points1
        elif g.winner == 'team2':
            max_score += g.points2

    for p in players:
        prev_score = next((e['score'] for e in lb_entries if e['username'] == p.username), 0)
        if round(p.profile.score - prev_score, 1) == round(max_score, 1):
            p.profile.score += 10
        p.save()

    completed_week = settings.week
    settings.week += 1
    settings.scrape_week = settings.week
    settings.publish = False
    settings.edit = True
    settings.lock_picks = False
    settings.first_game_dt = None
    settings.auto_lock_dt = None
    settings.auto_scrape_dt = _next_weekday_hour(settings.auto_scrape_weekday, settings.auto_scrape_hour, settings.auto_scrape_minute)
    if settings.lock_mode == 'manual' and settings.auto_lock_dt:
        settings.auto_lock_dt += timedelta(days=7)
    settings.save()

    recap = build_recap(completed_week)
    if recap:
        settings.refresh_from_db()
        settings.weekly_recap = recap
        settings.save()
        WeeklyLeaderboard.objects.filter(week=completed_week).update(recap=recap)

    log.info('Auto: advanced to week %s', settings.week)


def auto_tick():
    settings = SiteSettings.get()
    if not settings.auto_enabled:
        return

    now = datetime.now(timezone.utc)
    scrape_dt_str = settings.auto_scrape_dt.strftime('%m/%d %H:%M') if settings.auto_scrape_dt else '—'
    lock_dt_str = settings.auto_lock_dt.strftime('%m/%d %H:%M') if settings.auto_lock_dt else '—'
    print(f'[auto_tick] {now.strftime("%H:%M")} UTC | week={settings.week} publish={settings.publish} lock={settings.lock_picks} scrape_dt={scrape_dt_str} lock_dt={lock_dt_str}', flush=True)

    # 1. Scrape + publish when auto_scrape_dt has passed
    if not settings.publish and settings.auto_scrape_dt and now >= settings.auto_scrape_dt:
        do_scrape_and_publish(settings)
        settings.refresh_from_db()

    # 2. Lock picks when auto_lock_dt has passed
    if settings.publish and not settings.lock_picks and settings.auto_lock_dt and now >= settings.auto_lock_dt:
        do_lock_picks(settings)
        settings.refresh_from_db()

    # 3. Grade while locked; advance when all done
    if settings.lock_picks:
        games = list(Game.objects.filter(week=settings.week))
        if games and not all(g.graded for g in games):
            do_grade(settings)
        elif games and all(g.graded for g in games):
            do_advance_week(settings)
