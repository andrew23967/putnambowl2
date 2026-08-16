"""Verify migration 0010 does not lose league history.

0010 drops the History table. It rebuilds Game/Pick rows from the archive
first, and this script proves that round-trip on a real archive before the
migration is ever pointed at production.

It builds a throwaway database at main/0009 (the state production is in until
0010 ships), seeds it from a legacy History dump plus a live in-progress week,
runs the migration, and checks nothing was lost.

Usage:
    python scripts/verify_history_migration.py [path/to/legacy/db.sqlite3]

The legacy database defaults to ../legacy/db.sqlite3 (the original site's
export). See ../legacy/README.md.
"""
import json
import os
import sqlite3
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(PROJ)
LEGACY_DB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'legacy', 'db.sqlite3')
PY = sys.executable
TESTDB = os.path.join(PROJ, 'scripts', 'testmig.sqlite3')

if not os.path.exists(LEGACY_DB):
    sys.exit(f'legacy database not found: {LEGACY_DB}')

if os.path.exists(TESTDB):
    os.remove(TESTDB)

env = dict(os.environ)
env['DATABASE_URL'] = f'sqlite:///{TESTDB}'
env['PYTHONIOENCODING'] = 'utf-8'


def run(args, **kw):
    r = subprocess.run([PY] + args, cwd=PROJ, env=env, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        print('FAILED:', ' '.join(args))
        print(r.stdout[-4000:])
        print(r.stderr[-4000:])
        sys.exit(1)
    return r.stdout


# 1. Migrate to the state production is actually in today (main @ 0009).
print('== migrating to main/0009 ==')
run(['manage.py', 'migrate', 'main', '0009'])
run(['manage.py', 'migrate', 'accounts'])
run(['manage.py', 'migrate', 'auth'])
run(['manage.py', 'migrate', 'contenttypes'])
run(['manage.py', 'migrate', 'sessions'])

# 2. Seed it the way production looks: legacy History rows + matching users,
#    plus a handful of live Game/Pick rows for the in-progress week.
old = sqlite3.connect(LEGACY_DB)
old.row_factory = sqlite3.Row
hist_rows = old.execute('SELECT week, games_data, players_list FROM main_history ORDER BY week').fetchall()

usernames = set()
for r in hist_rows:
    gd = r['games_data']
    if isinstance(gd, str):
        gd = json.loads(gd or '[]')
    for g in gd:
        usernames.update((g.get('player_picks') or {}).keys())
print(f'archive: {len(hist_rows)} weeks, {len(usernames)} distinct players')

seed = {
    'hist': [(r['week'], r['games_data'] if isinstance(r['games_data'], str) else json.dumps(r['games_data']),
              r['players_list'] if isinstance(r['players_list'], str) else json.dumps(r['players_list']))
             for r in hist_rows],
    'users': sorted(usernames),
}
seed_path = os.path.join(os.path.dirname(TESTDB), 'seed.json')
with open(seed_path, 'w', encoding='utf-8') as f:
    json.dump(seed, f)

seed_script = f'''
import json
from django.contrib.auth.models import User
from main.models import SiteSettings, Game, Pick
seed = json.load(open(r"{seed_path}", encoding="utf-8"))
for u in seed["users"]:
    User.objects.get_or_create(username=u)
from django.db import connection
with connection.cursor() as c:
    for week, gd, pl in seed["hist"]:
        c.execute("INSERT INTO main_history (week, games_data, players_list) VALUES (%s,%s,%s)", [week, gd, pl])
    # SiteSettings and the live week must go in as raw SQL too: the DB is at
    # 0009 but models.py already describes the post-0011 schema.
    c.execute(
        "INSERT INTO main_sitesettings (id, week, publish, edit, lock_picks, multiplier,"
        " scrape_week, grade_api, weekly_recap, auto_enabled, auto_scrape_weekday,"
        " auto_scrape_hour, auto_scrape_minute, auto_lock_offset_minutes, lock_mode,"
        " auto_tz, first_game_dt, tick_interval, auto_scrape_dt, auto_lock_dt,"
        " scrape_filter_from_day, scrape_filter_to_day)"
        " VALUES (1, 23, 1, 0, 1, 1, 23, 'nfl_data_py', '', 0, 1, 9, 0, 10, 'offset',"
        " 'UTC', NULL, 300, NULL, NULL, NULL, NULL)"
    )
    c.execute(
        "INSERT INTO main_game (team1, team2, points1, points2, winner, graded, home_team, game_id, game_dt)"
        # team1 is the favorite (1.0), team2 the underdog (2.5)
        " VALUES ('Chicago Bears','Green Bay Packers',1.0,2.5,'',0,1,'',NULL)"
    )
    c.execute("SELECT id FROM main_game")
    gid = c.fetchall()[0][0]
    c.execute("SELECT id FROM auth_user LIMIT 1")
    uid = c.fetchall()[0][0]
    c.execute("INSERT INTO main_pick (user_id, game_id, choice) VALUES (%s,%s,'team1')", [uid, gid])
    c.execute("SELECT COUNT(*) FROM main_game")
    print("LIVEGAMEID", gid)
    print("seeded", c.fetchall()[0][0], "live games, week 23")
'''
sp = os.path.join(os.path.dirname(TESTDB), 'seed_run.py')
open(sp, 'w', encoding='utf-8').write(seed_script)
print('== seeding ==')
seed_out = run(['manage.py', 'shell', '-c', f'exec(open(r"{sp}", encoding="utf-8").read())'])
live_game_id = next(l.split()[1] for l in seed_out.splitlines() if l.startswith('LIVEGAMEID'))
print(seed_out.strip().splitlines()[-1], f'(live game id={live_game_id})')

# Record expected totals straight from the archive, independent of the migration.
expected_games = 0
expected_picks = 0
expected_week_pts = {}
for r in hist_rows:
    gd = r['games_data']
    if isinstance(gd, str):
        gd = json.loads(gd or '[]')
    for g in gd:
        if not g.get('team1') or not g.get('team2'):
            continue
        expected_games += 1
        for uname, pd in (g.get('player_picks') or {}).items():
            if isinstance(pd, dict) and pd.get('choice', pd.get('pick')) in ('0', '1', 'team1', 'team2'):
                expected_picks += 1
        # points the winner was worth, in archive terms
        w = g.get('winner')
        pts = g.get('points1') if w == 'team1' else (g.get('points2') if w == 'team2' else 0)
        expected_week_pts[r['week']] = round(expected_week_pts.get(r['week'], 0) + (pts or 0), 2)

# 3. Run the migration under test.
print('== running 0010 + 0011 ==')
run(['manage.py', 'migrate'])

# 4. Verify.
verify = f'''
from main.models import Game, Pick, SiteSettings
from django.db import connection
print("RESULT_games", Game.objects.count())
print("RESULT_picks", Pick.objects.count())
print("RESULT_weeks", sorted(set(Game.objects.values_list("week", flat=True))))
print("RESULT_liveweek", list(Game.objects.filter(pk={live_game_id}).values_list("week", flat=True)))
# favorite-first invariant: team1 is the favorite, so points1 <= points2
from django.db.models import F
print("RESULT_inverted", Game.objects.filter(points1__gt=F("points2")).count())
# max attainable points per week, recomputed from the rebuilt rows
tot = {{}}
for g in Game.objects.all():
    p = g.points1 if g.winner == "team1" else (g.points2 if g.winner == "team2" else 0)
    tot[g.week] = round(tot.get(g.week, 0) + p, 2)
print("RESULT_weekpts", tot)
print("RESULT_historygone", "main_history" not in connection.introspection.table_names())
'''
vp = os.path.join(os.path.dirname(TESTDB), 'verify.py')
open(vp, 'w', encoding='utf-8').write(verify)
out = run(['manage.py', 'shell', '-c', f'exec(open(r"{vp}", encoding="utf-8").read())'])

got = {}
for line in out.splitlines():
    if line.startswith('RESULT_'):
        k, _, v = line.partition(' ')
        got[k] = v

print()
print('== assertions ==')
ok = True


def check(label, actual, expected):
    global ok
    good = str(actual) == str(expected)
    ok = ok and good
    print(f'  [{"PASS" if good else "FAIL"}] {label}: got {actual} expected {expected}')


check('games reconstructed (+1 live)', got.get('RESULT_games'), expected_games + 1)
check('picks reconstructed (+1 live)', got.get('RESULT_picks'), expected_picks + 1)
check('team1 is favorite in every row', got.get('RESULT_inverted'), 0)
check('live game moved to current week', got.get('RESULT_liveweek'), '[23]')
check('History table dropped', got.get('RESULT_historygone'), 'True')

# per-week max points must match the archive exactly (proves winner+points
# survived the underdog-first swap)
weekpts = eval(got.get('RESULT_weekpts', '{}'))
weekpts.pop(23, None)
mismatch = {w: (weekpts.get(w), expected_week_pts.get(w))
            for w in expected_week_pts if abs(weekpts.get(w, 0) - expected_week_pts[w]) > 0.011}
check('per-week winning points preserved', mismatch or 'all match', 'all match')

print()
print('OVERALL:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
