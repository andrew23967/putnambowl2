"""Add Game.week and convert the History archive into real Game/Pick rows.

Before this migration the app stored each completed week as a JSON blob in
History, then deleted every Game and Pick row. Afterwards Game/Pick rows
persist forever and carry a `week` number. Dropping History without moving
its contents across would permanently lose every past week, so the RunPython
step below rebuilds Game/Pick rows from the archive first.

Both History formats in the wild agree on team order — team1 is the FAVORITE
(worth 1.0x the multiplier) and team2 the UNDERDOG (worth more) — so teams,
points and winners carry across untouched. Only the pick encoding differs:

  * legacy (imported from the original site via convert_history_simple.py)
    encodes picks as '0' (team1) / '1' (team2).
  * current (written by the old `nextweek` handler) encodes them as
    'team1' / 'team2'.

Kickoff times, ESPN game ids and home/away are not present in the archive and
are left at their defaults on reconstructed games.
"""
from django.db import migrations, models


def _normalise_pick(raw):
    """Map any archived pick encoding onto 'team1' / 'team2' / None."""
    if raw in ('team1', 'team2'):
        return raw
    if raw == '0':
        return 'team1'
    if raw == '1':
        return 'team2'
    return None


def _normalise_game(g):
    """Return (team1, team2, points1, points2, winner, {username: choice})."""
    team1 = g.get('team1') or ''
    team2 = g.get('team2') or ''
    try:
        points1 = float(g.get('points1') or 0)
    except (TypeError, ValueError):
        points1 = 0.0
    try:
        points2 = float(g.get('points2') or 0)
    except (TypeError, ValueError):
        points2 = 0.0
    winner = g.get('winner') or ''

    picks = {}
    for username, pd in (g.get('player_picks') or {}).items():
        if not isinstance(pd, dict):
            continue
        choice = _normalise_pick(pd.get('choice', pd.get('pick')))
        if choice:
            picks[username] = choice

    return team1, team2, points1, points2, winner, picks


def history_to_games(apps, schema_editor):
    History = apps.get_model('main', 'History')
    Game = apps.get_model('main', 'Game')
    Pick = apps.get_model('main', 'Pick')
    SiteSettings = apps.get_model('main', 'SiteSettings')
    User = apps.get_model('auth', 'User')

    # Games that already exist belong to the week currently in progress, not
    # to week 1 as the AddField default assumed.
    current = SiteSettings.objects.filter(pk=1).first()
    if current:
        current_week = current.week
    else:
        # No settings row (fresh install). Park live games past the archive so
        # they can never collide with a week being reconstructed below.
        last_archived = History.objects.order_by('-week').values_list('week', flat=True).first()
        current_week = (last_archived or 0) + 1
    Game.objects.all().update(week=current_week)

    user_ids = {u.username: u.pk for u in User.objects.all()}
    occupied = set(Game.objects.values_list('week', flat=True))

    for hist in History.objects.order_by('week'):
        week = hist.week
        # Never write into a week that already holds live Game rows.
        if week in occupied:
            continue

        games_data = hist.games_data or []
        if isinstance(games_data, str):
            import json
            try:
                games_data = json.loads(games_data)
            except ValueError:
                continue

        new_picks = []
        for g in games_data:
            if not isinstance(g, dict):
                continue
            team1, team2, points1, points2, winner, picks = _normalise_game(g)
            if not team1 or not team2:
                continue
            game = Game.objects.create(
                team1=team1,
                team2=team2,
                points1=points1,
                points2=points2,
                winner=winner,
                graded=bool(winner),
                week=week,
            )
            for username, choice in picks.items():
                uid = user_ids.get(username)
                if uid is None:
                    continue
                new_picks.append(Pick(user_id=uid, game_id=game.pk, choice=choice))

        Pick.objects.bulk_create(new_picks, batch_size=500)


def games_to_history(apps, schema_editor):
    """Reverse step: History is recreated empty rather than repopulated.

    Going backwards drops the week column, which is the only thing that makes
    the reconstructed rows meaningful, so there is nothing faithful to write
    back. The forward data is left in Game/Pick.
    """
    return


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0009_scrape_filter_days'),
    ]

    operations = [
        migrations.AddField(
            model_name='game',
            name='week',
            field=models.IntegerField(default=1),
        ),
        migrations.RunPython(history_to_games, games_to_history),
        migrations.DeleteModel(
            name='History',
        ),
    ]
