import logging
from datetime import datetime, timezone, timedelta

from django.contrib.auth.models import User
from django.db.models import Q

from .models import SiteSettings, Game, Pick, History, WeeklyLeaderboard
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
    """Generate recap text for the given week. Returns string or None if no history."""
    try:
        hist = History.objects.get(week=week)
    except History.DoesNotExist:
        return None

    games_data = hist.games_data
    player_scores = {}
    for g in games_data:
        for username, pd in g.get('player_picks', {}).items():
            player_scores[username] = round(player_scores.get(username, 0) + pd.get('points', 0), 1)

    ranked = sorted(player_scores.items(), key=lambda x: -x[1])
    if not ranked:
        return None

    winner_name = ranked[0][0]
    winner_pts = ranked[0][1]
    total_players = len(ranked)
    league_avg = round(sum(s for _, s in ranked) / max(total_players, 1), 1)
    last_place_name = ranked[-1][0]
    last_place_pts = ranked[-1][1]
    second_name = ranked[1][0] if len(ranked) > 1 else None
    second_pts = ranked[1][1] if len(ranked) > 1 else 0

    upsets, locks = [], []
    for g in games_data:
        w = g.get('winner')
        p1, p2 = g.get('points1', 0), g.get('points2', 0)
        t1, t2 = g.get('team1', ''), g.get('team2', '')
        if w == 'team2' and p2 > p1 * 1.5:
            upsets.append((t2, t1, p2))
        elif w == 'team1' and p1 > p2 * 1.5:
            upsets.append((t1, t2, p1))
        elif w == 'team1' and p1 <= p2:
            locks.append((t1, t2))
        elif w == 'team2' and p2 <= p1:
            locks.append((t2, t1))

    biggest_upset = max(upsets, key=lambda x: x[2]) if upsets else None
    burned = []
    if biggest_upset:
        upset_winner, upset_loser, _ = biggest_upset
        for g in games_data:
            t1, t2 = g.get('team1', ''), g.get('team2', '')
            if upset_winner in (t1, t2):
                loser_choice = 'team1' if upset_winner == t2 else 'team2'
                for u, pd in g.get('player_picks', {}).items():
                    if pd.get('pick') == loser_choice:
                        burned.append(u)

    p1_text = (
        f"Week {week} is in the books. {winner_name} took the week with {winner_pts} points"
        + (f", edging out {second_name} who finished with {second_pts}" if second_name else "")
        + f". The league average was {league_avg} points — if you're reading this hoping it was higher, it wasn't."
    )

    if upsets:
        upset_strs = [f"{w} over {l} ({pts} pts on the table)" for w, l, pts in upsets]
        p2_text = (
            f"The week had {'an upset' if len(upsets) == 1 else f'{len(upsets)} upsets'} worth noting: "
            + "; ".join(upset_strs) + ". "
            + (f"Anyone who had {biggest_upset[1]} in that spot is probably still thinking about it. "
               if biggest_upset else "")
            + (f"That includes {', '.join(burned)}, who learned the hard way." if burned else "")
        )
    else:
        chalk_strs = [f"{w} over {l}" for w, l in locks[:3]]
        p2_text = (
            "Chalk week — the favorites mostly held. "
            + (f"Results like {', '.join(chalk_strs)} kept things predictable. " if chalk_strs else "")
            + "When the favorites win every game it separates the people who actually did research from the ones who just picked their gut."
        )

    p3_text = (
        f"{last_place_name} finished last this week with {last_place_pts} points"
        + (f", which is {round(league_avg - last_place_pts, 1)} below average" if last_place_pts < league_avg else "")
        + ". "
        + "The standings are always one good week away from a shakeup, so nobody's safe. "
        + "New picks drop soon — don't blow it."
    )

    return f"{p1_text}\n\n{p2_text}\n\n{p3_text}"


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
    games_data = scrape_module.scrape(week=settings.week, api_type=settings.grade_api, year=year)
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
        if Game.objects.filter(Q(game_id=game_id) | Q(team1=team1, team2=team2)).exists():
            continue
        ug_ml, fav_ml = g[2], g[3]
        pts2 = (_calculate_points(ug_ml, abs(fav_ml)) * settings.multiplier
                if ug_ml and fav_ml else float(settings.multiplier))
        Game.objects.create(
            team1=team1, team2=team2,
            points1=float(settings.multiplier), points2=pts2,
            home_team=g[4], game_id=game_id, game_dt=game_dt
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

    try:
        from .email_utils import send_picks_published_email
        send_picks_published_email(settings)
    except Exception as e:
        log.error('Email send failed: %s', e)

    return added


def do_lock_picks(settings):
    settings.lock_picks = True
    settings.save()
    log.info('Auto: picks locked for week %s', settings.week)


def do_grade(settings, year=None):
    year = year or _current_season_year()
    results = scrape_module.grade(week=settings.week, api_type=settings.grade_api, year=year)
    graded = 0
    for game in Game.objects.filter(graded=False):
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
        log.info('Auto: graded %s game(s) for week %s', graded, settings.week)
    return graded


def do_advance_week(settings):
    games = list(Game.objects.all())
    players = list(User.objects.select_related('profile').all())
    all_picks = {(p.user_id, p.game_id): p for p in Pick.objects.filter(game__in=games)}

    lb_entries = [{'username': p.username, 'score': round(p.profile.score, 1)} for p in players]
    WeeklyLeaderboard.objects.update_or_create(week=settings.week, defaults={'entries': lb_entries})

    games_data = []
    players_list = [p.username for p in players]
    max_score = 0

    for g in games:
        player_picks = {}
        for p in players:
            pick = all_picks.get((p.id, g.id))
            if pick:
                correct = pick.is_correct
                pts = pick.points_earned if correct else 0
                if correct:
                    p.profile.score += pts
                player_picks[p.username] = {'pick': pick.choice, 'correct': bool(correct), 'points': pts}
            else:
                player_picks[p.username] = {'pick': None, 'correct': False, 'points': 0}

        if g.winner == 'team1':
            max_score += g.points1
        elif g.winner == 'team2':
            max_score += g.points2

        games_data.append({
            'team1': g.team1, 'team2': g.team2,
            'points1': g.points1, 'points2': g.points2,
            'winner': g.winner, 'player_picks': player_picks,
        })

    History.objects.update_or_create(
        week=settings.week,
        defaults={'games_data': games_data, 'players_list': players_list}
    )

    for p in players:
        prev_score = next((e['score'] for e in lb_entries if e['username'] == p.username), 0)
        if round(p.profile.score - prev_score, 1) == round(max_score, 1):
            p.profile.score += 10
        p.save()  # triggers post_save signal which saves profile

    completed_week = settings.week
    Pick.objects.all().delete()
    Game.objects.all().delete()
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
        games = list(Game.objects.all())
        if games and not all(g.graded for g in games):
            do_grade(settings)
        elif games and all(g.graded for g in games):
            do_advance_week(settings)
