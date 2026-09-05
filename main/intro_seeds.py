"""The starter intro library every new league gets.

Empty libraries do not get used - the first week someone opens the page they
write something in the free-text box and never come back. A handful of real
ones makes the feature visible and gives a house style to edit.

Migration 0025 seeded these for the original league and keeps its own copy;
`seed_intro_templates` is what the site admin's "create league" runs.
`{week}` and `{league}` are substituted when the mail is built.
"""

SEEDS = [
    ('Standard week',
     "Week {week} is live. Get your picks in before the first kickoff - "
     "a partial ballot scores nothing, so make sure every game has a pick."),

    ('Season opener',
     "Welcome to a new season of {league}.\n\n"
     "Week {week} is up and the slate is open. A reminder of how it works: "
     "favorites are worth 1 point, underdogs are worth more depending on the "
     "money line, and picking every game right in a week is worth a 10-point "
     "bonus on top."),

    ('Short week',
     "Week {week} is live, and it is a short one - check the kickoff times "
     "before you sit on your ballot. Picks lock before the first game, not "
     "before the Sunday games."),

    ('Tight at the top',
     "Week {week} is live.\n\n"
     "The table is close enough at the top that a good week moves you several "
     "places and a bad one costs you the same. Underdogs are where the ground "
     "gets made up - that is the whole point of the money line scoring."),

    ('Runaway leader',
     "Week {week} is live.\n\n"
     "Someone is well clear at this point, which means chalk picks are no "
     "longer good enough for everyone else. Favorites keep you where you are; "
     "underdogs are the only way to close a gap."),

    ('Playoff push',
     "Week {week} is live. We are into the part of the season where teams "
     "start resting starters and the money line stops meaning quite what it "
     "usually does. Read the injury report before you pick."),

    ('Final regular season week',
     "Week {week} is the last of the regular season.\n\n"
     "Big Loser picks settle after this one, and anyone who took a team that "
     "sneaks into the playoffs is about to find out what that costs. Last "
     "chance to move before the points start doubling."),

    ('Playoffs begin',
     "Week {week} - the playoffs.\n\n"
     "Every point is doubled from here. Picking the favorite is worth 2, and "
     "the underdogs are worth double whatever the money line says. The table "
     "can turn over completely in the next few weeks."),

    ('Super Bowl',
     "Week {week}. One game left.\n\n"
     "Points are quadrupled for the Super Bowl, so this is not over for "
     "anybody. Conference champion and Super Bowl winner picks from the "
     "preseason settle after this too."),

    ('Quiet week',
     "Week {week} is live. Nothing much to report - get your picks in."),
]


def seed_intro_templates(league):
    """Add the starter set to a league; existing names are left alone."""
    from .models import IntroTemplate
    made = 0
    for name, body in SEEDS:
        _, created = IntroTemplate.objects.get_or_create(
            league=league, name=name, defaults={'body': body})
        made += 1 if created else 0
    return made
