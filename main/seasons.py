"""The season archive.

"Save season & reset" deletes every Game, Pick and WeeklyLeaderboard row, so the
`SeasonRecord` written just before that is the only record a past season leaves.
It therefore carries everything the Seasons page and the members' finishes need:
the final table with ranks and records, each player's preseason picks, and the
week-by-week points series.

The record used to be written only when the form validated - and the form never
could, because the page posted no year - while the reset ran regardless. So the
button wiped the season and kept nothing. `archive_and_reset` does both inside
one transaction, or neither.
"""
from django.contrib.auth.models import User
from django.db import transaction

from .models import Game, Pick, SeasonRecord, WeeklyLeaderboard
from .rankings import competition_ranks


def build_season_record(settings, year, notes=''):
    """Snapshot the season as it stands into a `SeasonRecord` and return it."""
    league = settings.league
    players = list(User.objects.select_related('profile').filter(profile__league=league))
    graded_ids = set(Game.objects.filter(league=league, graded=True).exclude(winner='')
                     .values_list('id', flat=True))
    correct, graded = {}, {}
    for pick in (Pick.objects.filter(game_id__in=graded_ids)
                 .select_related('game')):
        graded[pick.user_id] = graded.get(pick.user_id, 0) + 1
        if pick.is_correct:
            correct[pick.user_id] = correct.get(pick.user_id, 0) + 1

    scores = {p.username: round(p.profile.score, 1) for p in players}
    ranks = competition_ranks(scores)
    standings = []
    for p in players:
        prof = p.profile
        standings.append({
            'username': p.username,
            'display_name': prof.display_name,
            'score': scores[p.username],
            'rank': ranks[p.username],
            'correct': correct.get(p.id, 0),
            'graded': graded.get(p.id, 0),
            'is_bot': prof.is_bot,
            'preseason': {
                'big_loser': prof.big_loser,
                'nfc': prof.nfc_champ,
                'afc': prof.afc_champ,
                'superbowl': prof.superbowl_winner,
            } if prof.preseason_submitted else None,
        })
    standings.sort(key=lambda e: (-e['score'], e['username']))

    # WeeklyLeaderboard(week=k) holds the table going *into* week k. Closing the
    # series with the live scores gives "after the final week" a row too, so a
    # chart can read score-after-week-k from entry k+1 all the way to the end.
    weekly = [{'week': lb.week, 'entries': lb.entries}
              for lb in WeeklyLeaderboard.objects.filter(league=league).order_by('week')]
    last_week = weekly[-1]['week'] if weekly else 0
    weekly.append({'week': last_week + 1,
                   'entries': [{'username': e['username'], 'score': e['score']}
                               for e in standings]})

    winner = standings[0]['username'] if standings else ''
    record, _ = SeasonRecord.objects.update_or_create(
        league=league, year=year,
        defaults={
            'winner_username': winner,
            'final_standings': standings,
            'notes': notes or '',
            'weeks': len(weekly) - 1,
            'weekly': weekly,
        },
    )
    return record


def archive_and_reset(settings, year, notes=''):
    """Write the season's record, then clear the board for the next one.

    One transaction: if the record cannot be written, nothing is deleted.
    The Emails feed is league correspondence, not season data, and is kept.
    """
    with transaction.atomic():
        record = build_season_record(settings, year, notes)

        league = settings.league
        for p in User.objects.select_related('profile').filter(profile__league=league):
            p.profile.score = 0
            p.profile.preseason_submitted = False
            p.profile.save(update_fields=['score', 'preseason_submitted'])
        Pick.objects.filter(game__league=league).delete()
        Game.objects.filter(league=league).delete()
        WeeklyLeaderboard.objects.filter(league=league).delete()

        settings.week = 1
        settings.scrape_week = 1
        settings.publish = False
        settings.lock_picks = False
        settings.first_game_dt = None
        settings.auto_lock_dt = None
        settings.auto_scrape_dt = None
        settings.auto_grade_dt = None
        settings.auto_first_attempt_dt = None
        settings.auto_last_issue = ''
        settings.weekly_intro = ''
        settings.weekly_recap = ''
        settings.reminder_sent_week = 0
        settings.save()
    return record


def finishes_by_username(league):
    """{username: [{year, rank, players}, ...]} newest season first.

    Reads ranks off the record where they were stored, and recomputes them for
    records written before ranks were kept.
    """
    out = {}
    for record in SeasonRecord.objects.filter(league=league).order_by('-year'):
        entries = [e for e in (record.final_standings or []) if e.get('username')]
        if not entries:
            continue
        ranks = competition_ranks({e['username']: e.get('score', 0) for e in entries})
        for e in entries:
            out.setdefault(e['username'], []).append({
                'year': record.year,
                'rank': e.get('rank') or ranks[e['username']],
                'players': len(entries),
            })
    return out
