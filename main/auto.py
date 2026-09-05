import logging
from datetime import datetime, timezone, timedelta

from django.contrib.auth.models import User
from django.db.models import Min

from .models import SiteSettings, Game, Pick, WeeklyLeaderboard
from .scoring import calculate_points
from .teams import (TEAM_ABBREV, canonical_abbrev,
                    canonical_game_id as _canon_game_id, team_from_abbrev)
from . import scrape as scrape_module

log = logging.getLogger(__name__)

WEEKDAY_NAMES = {0: 'Monday', 1: 'Tuesday', 2: 'Wednesday', 3: 'Thursday',
                 4: 'Friday', 5: 'Saturday', 6: 'Sunday'}


def _current_season_year():
    """Season year for "now" — see scrape.current_season_year for the rule."""
    return scrape_module.current_season_year()


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


DEFAULT_RECAP_PROMPT = (
    "You are the commissioner of a private NFL pick'em fantasy league called "
    "PutnamBowl.\n"
    "Write a factual weekly recap for Week {week} in 3 short paragraphs. Report "
    "what happened: who won, who lost, the scores, and how people's picks went. "
    "Straightforward and informative — no jokes, no sarcasm, no filler."
)

# Appended after the editable instructions and the data, never editable. Without
# it the model may answer in markdown, which the plain-text emails render raw.
RECAP_FORMAT_RULES = (
    'Write the recap now. Plain text only, no markdown, no headers.'
)


def recap_data_block(week):
    """The facts a recap is written from.

    Delegates to `recap_stats`, which computes the angles - best week, biggest
    mover, the game that caught everyone out, who is within a point of whom -
    rather than handing the model every pick and hoping it spots them. See that
    module for why.

    Returned separately from the prompt so the Emails page can show exactly what
    gets included.
    """
    from . import recap_stats
    return recap_stats.data_block(week)


def build_recap_prompt(week, instructions=None):
    """Editable instructions, then the data, then the format rules."""
    from .models import SiteSettings

    block, _ = recap_data_block(week)
    if block is None:
        return None
    if instructions is None:
        instructions = (SiteSettings.get().recap_prompt or '').strip()
    instructions = instructions or DEFAULT_RECAP_PROMPT
    # replace() not format(): the text is user-edited, and a stray brace should
    # not raise.
    instructions = instructions.replace('{week}', str(week))
    return f'{instructions}\n\n{block}\n\n{RECAP_FORMAT_RULES}'


def build_recap(week):
    """The week's recap: Gemini if it is available, a plain summary if not.

    The facts come from `recap_stats`, the same place the prompt gets them. This
    used to recompute per-player scores here as well - a second loop over every
    pick, building a `game_lines` list nothing read - and then gate the whole
    recap on `if not ranked: return None`. A week where nobody had submitted
    picks produced no recap at all, even though there was plenty to report:
    results, upsets, who is still level at the top.
    """
    from . import recap_stats

    block, ranked = recap_stats.data_block(week)
    if not ranked:
        return None

    prompt = build_recap_prompt(week)
    if prompt:
        try:
            from django.conf import settings as django_settings
            api_key = getattr(django_settings, 'GEMINI_API_KEY', '')
            if api_key:
                from google import genai
                from .ai_picks import model_name
                client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model=model_name(),
                    contents=prompt,
                )
                log.info('Gemini recap generated for week %s', week)
                return response.text.strip()
        except Exception as e:
            log.error('Gemini recap failed: %s', e)

    # Fallback, when Gemini is unavailable or errors.
    league_avg = round(sum(pts for _, pts in ranked) / len(ranked), 1)
    winner_name, winner_pts = ranked[0]
    last_place_name, last_place_pts = ranked[-1]
    second_name = ranked[1][0] if len(ranked) > 1 else None
    second_pts = ranked[1][1] if len(ranked) > 1 else 0

    p1 = (f"Week {week} is in the books. {winner_name} took the week with {winner_pts} points"
          + (f", edging out {second_name} who finished with {second_pts}" if second_name else "")
          + f". League average was {league_avg} points.")
    p2 = f"{last_place_name} finished last with {last_place_pts} points. Better luck next week."
    return f"{p1}\n\n{p2}"


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


def _game_day_allowed(game_dt, day_set, tz_str='UTC'):
    """Is this kickoff on a day the league plays?

    `day_set` is a set of weekday numbers; empty means no filter. This replaced a
    contiguous from/to range, which could not express "Sunday and Monday but not
    Saturday" — the wrap-around branch quietly included every day in between.

    A game with no kickoff time is kept: dropping it would silently shrink the
    slate, and an unknown time is a data problem to surface, not to filter away.
    """
    if not day_set or game_dt is None:
        return True
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_str or 'UTC')
    except Exception:
        tz = timezone.utc
    return game_dt.astimezone(tz).weekday() in day_set


def scrape_week_games(settings, year=None):
    """Pull the week's slate into the database. Does not publish.

    Returns a report the caller validates before deciding to publish:
    ``added``, ``stored``, ``unpriced`` (matchups the source gave no moneyline
    for) and ``cross_check`` (how many games the *other* source sees, or None).

    Split out of do_scrape_and_publish so a bad scrape can be inspected before
    anything is mailed. The old version set ``publish = True`` unconditionally, so
    a source outage published an empty week and emailed the league about it.
    """
    year = year or _current_season_year()

    week_type = scrape_module.get_week_type(settings.week, year)
    auto_multiplier = {'regular': 1, 'playoffs': 2, 'superbowl': 4}[week_type]
    if settings.multiplier != auto_multiplier:
        log.info('Auto: multiplier -> %sx (%s, week %s)', auto_multiplier, week_type, settings.week)
        settings.multiplier = auto_multiplier

    games_data = scrape_module.scrape(week=settings.week, api_type=settings.scrape_api, year=year)
    day_set = settings.scrape_day_set()
    added = 0
    updated = 0
    unpriced = []

    for g in games_data:
        game_dt = g[6]
        if not _game_day_allowed(game_dt, day_set, settings.auto_tz):
            continue
        # team_from_abbrev, not a bare dict lookup: 'LA' is not a key in
        # ABBREV_TO_TEAM, so every Rams game was stored with the literal team
        # name "LA" - not a valid choice, and unmappable back to an abbreviation,
        # so grading could never match it.
        team1 = team_from_abbrev(g[0])
        team2 = team_from_abbrev(g[1])
        game_id = g[5]

        ml1, ml2 = g[2], g[3]
        priced = bool(ml1) and bool(ml2)
        if not priced:
            # Worth naming, not just counting: without a moneyline the underdog
            # scores the same as the favorite, which voids the whole point of the
            # league for that game.
            unpriced.append('%s vs %s' % (team1, team2))

        pts2 = (calculate_points(ml1, abs(ml2)) * settings.multiplier
                if priced else float(settings.multiplier))

        # Scope to this week - games persist across weeks, and division rivals
        # play the same matchup twice a season.
        existing = Game.match_existing(settings.week, team1, team2, game_id)
        if existing is not None:
            # Re-scraping refreshes the fixture in place rather than skipping it,
            # so a line that moved, a kickoff that got flexed, or a favorite that
            # flipped all land on the row that is already there.
            #
            # Points are frozen once picks lock: members pick against the numbers
            # they were shown, and rewriting them afterwards silently rescores the
            # week. Kickoff time still tracks, because the countdown and the lock
            # are computed from it.
            existing.game_dt = game_dt
            existing.game_id = game_id
            fields = ['game_dt', 'game_id']
            if not settings.lock_picks:
                existing.team1, existing.team2 = team1, team2
                existing.team1_is_home = g[4]
                existing.points1 = float(settings.multiplier)
                existing.points2 = pts2
                fields += ['team1', 'team2', 'team1_is_home', 'points1', 'points2']
            existing.save(update_fields=fields)
            updated += 1
            continue

        Game.objects.create(
            team1=team1, team2=team2,
            points1=float(settings.multiplier), points2=pts2,
            team1_is_home=g[4], game_id=game_id, game_dt=game_dt,
            week=settings.week,
        )
        added += 1

    return {
        'added': added,
        'updated': updated,
        'stored': Game.objects.filter(week=settings.week).count(),
        'unpriced': unpriced,
        'cross_check': _cross_check_count(settings, year, day_set),
    }


def _cross_check_count(settings, year, day_set):
    """How many games the *other* source sees for this week, or None.

    A source can return a plausible-looking short slate - a few games quietly
    missing rather than an obviously empty response - and nothing in the data
    itself reveals it. The second source is free (ESPN) and independent, so it is
    the only real check available. Never fatal: sources legitimately disagree
    around postponements, so this warns rather than blocks.
    """
    other = 'espn' if settings.scrape_api != 'espn' else 'nfl_data_py'
    try:
        rows = scrape_module.scrape(week=settings.week, api_type=other, year=year)
    except Exception as e:
        log.warning('Auto: cross-check via %s failed: %s', other, e)
        return None
    return sum(1 for r in rows
               if _game_day_allowed(r[6], day_set, settings.auto_tz))


def validate_slate(report):
    """Reasons this slate should not go out. Empty list means it is fine."""
    issues = []
    if report['stored'] == 0:
        issues.append('No games found for this week.')
    if report['unpriced']:
        n = len(report['unpriced'])
        issues.append(
            '%d game%s no moneyline, so the underdog scores the same as the '
            'favorite: %s' % (n, 's have' if n != 1 else ' has',
                              '; '.join(report['unpriced'][:6])))
    xc = report.get('cross_check')
    if xc is not None and report['stored'] and xc != report['stored']:
        issues.append(
            'Sources disagree: this one has %d games, the cross-check sees %d.'
            % (report['stored'], xc))
    return issues


def publish_week(settings, year=None):
    """Mark the week live, set the lock time, make bot picks and mail the league."""
    year = year or _current_season_year()
    # Lock against the earliest kickoff actually stored for the week, not the
    # week's true first game. With a scrape day filter set - a Sunday-only league
    # - the Thursday nighter never enters the slate, and pinning the lock to it
    # shut picks 2.7 days before the first game anyone could pick. The dashboard's
    # Scrape button has always computed it this way; now both paths agree.
    first_dt = Game.objects.filter(
        week=settings.week, game_dt__isnull=False
    ).aggregate(Min('game_dt'))['game_dt__min']
    if first_dt is None:
        first_dt = scrape_module.get_first_game_dt(week=settings.week, year=year)
    settings.first_game_dt = first_dt
    if settings.lock_mode == 'offset' and first_dt and settings.auto_lock_offset_minutes:
        settings.auto_lock_dt = first_dt - timedelta(minutes=settings.auto_lock_offset_minutes)
    settings.publish = True
    # Grading starts at the first kickoff. Nothing can be graded before then, so
    # there is nothing to poll for; and deriving it from the slate means a flexed
    # game moves it automatically, where a configured weekday and time would sit
    # there being wrong.
    settings.auto_grade_dt = first_dt
    settings.auto_first_attempt_dt = None
    settings.save()
    log.info('Auto publish: week %s, first kickoff %s, grading from %s',
             settings.week, first_dt, settings.auto_grade_dt)
    make_bot_picks(week=settings.week)

    try:
        from .email_utils import send_picks_published_email
        send_picks_published_email(settings)
    except Exception as e:
        log.error('Email send failed: %s', e)


def do_scrape_and_publish(settings, year=None, force=False):
    """Scrape the week, and publish it only if the slate looks right.

    A failed check does not publish. It records the problem and returns, leaving
    auto_tick to try again next tick; once ``auto_retry_window_minutes`` has
    passed since the first attempt it publishes anyway with the issue recorded, so
    a permanently degraded source cannot stall the season forever.

    ``force=True`` is the dashboard's manual Scrape button: a person pressing it
    is looking at the result, so it publishes regardless and just records what is
    wrong.
    """
    year = year or _current_season_year()
    report = scrape_week_games(settings, year)
    issues = validate_slate(report)
    now = datetime.now(timezone.utc)

    if issues and not force:
        if settings.auto_first_attempt_dt is None:
            settings.auto_first_attempt_dt = now
        waited = (now - settings.auto_first_attempt_dt).total_seconds() / 60
        window = settings.auto_retry_window_minutes or 0
        settings.auto_last_issue = ' | '.join(issues)

        if waited < window:
            settings.save()
            log.warning('Auto: week %s held back (%.0f of %.0f min into retry): %s',
                        settings.week, waited, window, settings.auto_last_issue)
            return report['added']

        log.error('Auto: retry window elapsed for week %s, publishing anyway: %s',
                  settings.week, settings.auto_last_issue)
        settings.save()
    else:
        settings.auto_last_issue = ' | '.join(issues) if issues else ''

    publish_week(settings, year)
    return report['added']


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
        # `team1_is_home` is the whole ballgame here: team1 is the favorite, so
        # which side is at home has to come off the flag, not off the ordering.
        home_side, away_side = (
            ('team1', 'team2') if game.team1_is_home else ('team2', 'team1'))
        home_name = game.team1 if game.team1_is_home else game.team2
        away_name = game.team2 if game.team1_is_home else game.team1
        stored_id = _canon_game_id(game.game_id)

        for r in results:
            game_id, outcome, home_abbrev, away_abbrev = r[0], r[1], r[2], r[3]
            # Primary: game_id, both sides folded to one format first. The two
            # sources spell the week and the Rams differently, so the raw strings
            # never compared equal.
            matched = bool(stored_id and stored_id == _canon_game_id(game_id))
            # Fallback for a game stored before the IDs agreed, or entered by hand.
            if not matched:
                matched = (
                    TEAM_ABBREV.get(home_name, '') == canonical_abbrev(home_abbrev)
                    and TEAM_ABBREV.get(away_name, '') == canonical_abbrev(away_abbrev))
            if not matched:
                continue

            if outcome == 'home':
                game.winner = home_side
            elif outcome == 'away':
                game.winner = away_side
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
        p.profile.save()

    completed_week = settings.week
    prev_lock_dt = settings.auto_lock_dt
    settings.week += 1
    settings.scrape_week = settings.week
    settings.publish = False
    settings.lock_picks = False
    settings.first_game_dt = None
    settings.auto_lock_dt = None
    if settings.lock_mode == 'manual' and prev_lock_dt:
        # A manual lock is a weekly clock, not a one-off. This used to read
        # auto_lock_dt *after* the line above had cleared it, so it never ran:
        # from the second week on there was no lock time, and the season
        # silently stalled - no lock, no reminder, no grading, no advance.
        next_lock = prev_lock_dt + timedelta(days=7)
        now = datetime.now(timezone.utc)
        while next_lock <= now:
            next_lock += timedelta(days=7)
        settings.auto_lock_dt = next_lock
    settings.auto_scrape_dt = _next_weekday_hour(settings.auto_scrape_weekday, settings.auto_scrape_hour, settings.auto_scrape_minute)
    # Clear the new week's grading and retry state too. Left set, the old
    # auto_grade_dt is already in the past, so grading would start the moment the
    # next week's picks locked - exactly the behaviour the grade time replaced -
    # and a stale auto_first_attempt_dt would count last week's retry window
    # against this week's first scrape, skipping the retries entirely.
    settings.auto_grade_dt = None
    settings.auto_first_attempt_dt = None
    settings.auto_last_issue = ''
    # Last week's note must not go out attached to this week's games, and the
    # reminder has to be able to fire again.
    settings.weekly_intro = ''
    settings.reminder_sent_week = 0
    settings.save()

    recap = build_recap(completed_week)
    if recap:
        settings.refresh_from_db()
        settings.weekly_recap = recap
        settings.save()
        WeeklyLeaderboard.objects.filter(week=completed_week).update(recap=recap)
        try:
            # Recorded in the feed, not mailed. The recap goes to the league at
            # the top of next week's picks-are-live email - one mail a week
            # rather than two, which is what people actually read.
            from .email_utils import record_recap_email
            record_recap_email(completed_week, recap)
        except Exception as e:
            # A recap that fails to record must not abort the advance.
            log.error('Recording the recap failed: %s', e)

    log.info('Auto: advanced to week %s', settings.week)


def auto_tick():
    settings = SiteSettings.get()
    if not settings.auto_enabled:
        return

    now = datetime.now(timezone.utc)

    def _fmt(dt):
        return dt.strftime('%m/%d %H:%M') if dt else '-'

    log.info('tick %s UTC | week=%s publish=%s lock=%s scrape=%s lock_dt=%s grade=%s',
             now.strftime('%H:%M'), settings.week, settings.publish, settings.lock_picks,
             _fmt(settings.auto_scrape_dt), _fmt(settings.auto_lock_dt),
             _fmt(settings.auto_grade_dt))

    # 0. Stop at the end of the season. Advancing used to be a bare `week += 1`,
    #    so after the Super Bowl the autopilot rolled into week 23, scraped an
    #    empty slate and mailed the league about it, every week, forever.
    if settings.week > settings.season_last_week:
        log.info('Auto: season complete (week %s past last week %s); standing down.',
                 settings.week, settings.season_last_week)
        return

    # 1. Scrape and publish once auto_scrape_dt has passed. do_scrape_and_publish
    #    decides for itself whether the slate is good enough to go out, and keeps
    #    retrying within the window, so this may run several ticks in a row.
    if not settings.publish and settings.auto_scrape_dt and now >= settings.auto_scrape_dt:
        do_scrape_and_publish(settings)
        settings.refresh_from_db()

    # 2. Lock picks when auto_lock_dt has passed.
    if settings.publish and not settings.lock_picks and settings.auto_lock_dt and now >= settings.auto_lock_dt:
        do_lock_picks(settings)
        settings.refresh_from_db()

    # 3. Nudge anyone whose ballot is still short, once, as the lock approaches.
    if settings.publish and not settings.lock_picks and settings.auto_lock_dt:
        due = settings.auto_lock_dt - timedelta(
            hours=settings.reminder_hours_before_lock or 0)
        if now >= due:
            try:
                from .email_utils import send_pick_reminder_email
                send_pick_reminder_email(settings)
            except Exception as e:
                # A reminder that fails must not stop the week locking.
                log.error('Sending the pick reminder failed: %s', e)
            settings.refresh_from_db()

    # 4. Grade from auto_grade_dt onward, then advance.
    if not settings.lock_picks:
        return

    games = list(Game.objects.filter(week=settings.week))
    if not games:
        return

    if not all(g.graded for g in games):
        # Grading used to start the instant picks locked, which meant polling the
        # source all through Sunday for results that could not exist yet. It now
        # waits for its own configured time and then polls each tick, which is
        # what carries it across Monday night.
        grade_dt = settings.auto_grade_dt
        if grade_dt and now < grade_dt:
            return
        do_grade(settings)
        settings.refresh_from_db()
        games = list(Game.objects.filter(week=settings.week))

    if not all(g.graded for g in games):
        return

    if not settings.auto_advance:
        log.info('Auto: week %s fully graded; holding (auto_advance is off).', settings.week)
        return

    if settings.week >= settings.season_last_week:
        # Score the final week, but do not open another one.
        do_advance_week(settings)
        settings.refresh_from_db()
        log.info('Auto: final week %s scored; season over.', settings.season_last_week)
        return

    do_advance_week(settings)
