"""The league the site ran as before there were leagues.

Unconditional: every database - production, a fresh checkout, the test runner -
gets a `putnambowl` league, so the main/accounts backfills that follow always
have something to point existing rows at. The rules are the commissioner's own
words from the old rules page, as plain paragraphs; edit them from the
dashboard.
"""
from django.db import migrations

RULES = """PUTNAM BOWL

Welcome to Putnam Bowl! Please read these rules carefully and direct any questions to The Office of The Commissioner for clarification.

Franchise Fees

Prior to Week 1, please ensure that you submit your $20 Franchise Fee to The Office of The Commissioner.

Paypal: mark.r.hutchinson@gmail.com

Snail Mail: Mark Hutchinson, 765 Greenwich Street, Apartment 4, NYC 10014

IF YOUR FRANCHISE FEE IS NOT RECEIVED BY WEEK 2, YOUR FRANCHISE WILL BE DISQUALIFIED.

In addition to the coveted Putnam Bowl, the winner will receive 70% of the pot. The second place team will receive 20% of the pot and the third place will receive 10%.

Scoring

We will be basing Putnam Points on the Vegas money line. You can view the money line we will be using at Vegas Insider: https://www.vegasinsider.com/nfl/odds/las-vegas/

The picks page of this website will let you know how many points each team is worth every week. As always, favorites will always be worth 1 PB point should they win. The number of points for the underdog will be calculated using the money line.

If you are interested, here's how the money line works. Take a hypothetical game between Miami and Tampa Bay, where the money line for Miami is +150 and -170 for Tampa Bay (as with the spread, + indicates underdogs and - indicates favorites). This means that you would have to bet $170 on Tampa Bay to win $100 and you would win $150 if you bet $100 on Miami and they won.

We will take the money line and convert it to PB Points. In the example above, Tampa Bay (and any favorite) will be worth 1 PB Point and Miami would be worth 1.58 PB Points. Converting this involves some math, which you don't need to know. If you are interested, however, we can send you the formula and a spreadsheet to calculate it yourself.

Additional Points

"Perfect Game" Bonus: Correctly picking every game in a given week (regular season only) is worth 10 points in addition to all other points earned that week.

In the playoffs all points for favorites and underdogs are doubled (i.e., picking the favored team is worth 2 points if they win).

In the Superbowl points are quadrupled (i.e., picking the favored team is worth 4 points if they win).

Picking Games

You can submit your picks for the upcoming week's games with their respective PB Points calculated as described above on the picks page of this website. Note: we will NOT pick Wednesday or Thursday night games, but you must pick a winner for every other game over the weekend and for the Monday night game(s), every week.

Picks for the weekend slate of games are due one hour before kick-off of the first game to be played (even if on a Saturday).

If we do not receive all of your picks on time for the weekend slate of games, then NONE of your picks will count, regardless of whether they are sent before their respective games. In other words, don't just send a pick for a Saturday game in anticipation of sending the rest of your picks later in the weekend. You will not receive credit for any picks that week.

If we do not receive your weekend slate picks ON TIME in any given week, you will receive a score for that week of 3 points less than the lowest score received by any other person that week. (So if the low scorer for the week gets 2 PB Points, you get minus 1.) You will also be ridiculed in the "Standings" e-mail, which goes out each Tuesday.

Pre-Season Picks

In addition to your picks for the first games of the season, which are due before week 1's picks lock, you must send your predictions for the following:

1. Big Loser - You must pick the team you think will end the regular season with the worst record in the NFL. A correct pick is worth 10 points at the end of the regular season; if there is a tie among 2+ teams for worst record, the point value for correctly picking one of them is divided by the number of teams that shared the dubious distinction. For example, if 2 teams finish 2-14 and you picked one as your big loser, you get 5 points.

2. Playoffs - You must also pick the teams you think will play in the Superbowl (the NFC and AFC champions) and the Superbowl winner (which must be one of your conference champion picks). A correct pick for conference champ is worth 5 points; a correct pick for Superbowl champ is worth 10 points.

3. Penalties - If the team you picked as your Big Loser makes the playoffs, you lose 5 points. If a team you picked as a Conference Champion fails to make the playoffs, you lose 5 points (minus 10 if both your conference champion picks do not make it).

Good luck!

The Office of The Commissioner"""


def forwards(apps, schema_editor):
    League = apps.get_model('leagues', 'League')
    League.objects.get_or_create(
        slug='putnambowl',
        defaults={'name': 'PutnamBowl', 'rules': RULES, 'is_active': True},
    )


def backwards(apps, schema_editor):
    League = apps.get_model('leagues', 'League')
    League.objects.filter(slug='putnambowl').delete()


class Migration(migrations.Migration):
    dependencies = [('leagues', '0001_initial')]
    operations = [migrations.RunPython(forwards, backwards)]
