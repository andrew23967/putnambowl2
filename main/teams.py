TEAMS = [
    ("Arizona Cardinals", "Arizona Cardinals"),
    ("Atlanta Falcons", "Atlanta Falcons"),
    ("Baltimore Ravens", "Baltimore Ravens"),
    ("Buffalo Bills", "Buffalo Bills"),
    ("Carolina Panthers", "Carolina Panthers"),
    ("Chicago Bears", "Chicago Bears"),
    ("Cincinnati Bengals", "Cincinnati Bengals"),
    ("Cleveland Browns", "Cleveland Browns"),
    ("Dallas Cowboys", "Dallas Cowboys"),
    ("Denver Broncos", "Denver Broncos"),
    ("Detroit Lions", "Detroit Lions"),
    ("Green Bay Packers", "Green Bay Packers"),
    ("Houston Texans", "Houston Texans"),
    ("Indianapolis Colts", "Indianapolis Colts"),
    ("Jacksonville Jaguars", "Jacksonville Jaguars"),
    ("Kansas City Chiefs", "Kansas City Chiefs"),
    ("Las Vegas Raiders", "Las Vegas Raiders"),
    ("Los Angeles Chargers", "Los Angeles Chargers"),
    ("Los Angeles Rams", "Los Angeles Rams"),
    ("Miami Dolphins", "Miami Dolphins"),
    ("Minnesota Vikings", "Minnesota Vikings"),
    ("New England Patriots", "New England Patriots"),
    ("New Orleans Saints", "New Orleans Saints"),
    ("New York Giants", "New York Giants"),
    ("New York Jets", "New York Jets"),
    ("Philadelphia Eagles", "Philadelphia Eagles"),
    ("Pittsburgh Steelers", "Pittsburgh Steelers"),
    ("San Francisco 49ers", "San Francisco 49ers"),
    ("Seattle Seahawks", "Seattle Seahawks"),
    ("Tampa Bay Buccaneers", "Tampa Bay Buccaneers"),
    ("Tennessee Titans", "Tennessee Titans"),
    ("Washington Commanders", "Washington Commanders"),
]

# Conference membership.
#
# These were TEAMS[:16] and TEAMS[16:] — a straight alphabetical cut, not a
# conference. The "NFC" half held the Ravens, Bills, Bengals, Browns, Broncos,
# Texans, Colts, Jaguars and Chiefs. Nothing consumed them, so nothing was
# visibly broken; the preseason form does now, which is why they are real.
#
# Listed by division so a franchise move or rename is easy to place, and derived
# from TEAMS so both halves keep its exact spellings and its (value, label) shape.
NFC_TEAM_NAMES = {
    # East
    "Dallas Cowboys", "New York Giants", "Philadelphia Eagles", "Washington Commanders",
    # North
    "Chicago Bears", "Detroit Lions", "Green Bay Packers", "Minnesota Vikings",
    # South
    "Atlanta Falcons", "Carolina Panthers", "New Orleans Saints", "Tampa Bay Buccaneers",
    # West
    "Arizona Cardinals", "Los Angeles Rams", "San Francisco 49ers", "Seattle Seahawks",
}

NFC_TEAMS = [t for t in TEAMS if t[0] in NFC_TEAM_NAMES]
AFC_TEAMS = [t for t in TEAMS if t[0] not in NFC_TEAM_NAMES]

TEAM_ABBREV = {
    "Arizona Cardinals": "ARI",
    "Atlanta Falcons": "ATL",
    "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR",
    "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN",
    "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN",
    "Detroit Lions": "DET",
    "Green Bay Packers": "GB",
    "Houston Texans": "HOU",
    "Indianapolis Colts": "IND",
    "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV",
    "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR",
    "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN",
    "New England Patriots": "NE",
    "New Orleans Saints": "NO",
    "New York Giants": "NYG",
    "New York Jets": "NYJ",
    "Philadelphia Eagles": "PHI",
    "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA",
    "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN",
    "Washington Commanders": "WAS",
}

ABBREV_TO_TEAM = {v: k for k, v in TEAM_ABBREV.items()}

# Abbreviations the data sources actually emit, which are NOT the canonical ones
# above. Every one of these was a silent failure: an unmapped abbreviation was
# stored raw, so a Rams game went into the database as the literal string "LA"
# instead of a team name, and `TEAM_ABBREV` could not map it back — which meant
# grading never matched that game.
#
#   nfl_data_py sends LA  for the Rams,      ESPN sends LAR
#   ESPN        sends WSH for the Commanders, nfl_data_py sends WAS
#
# so each source has at least one team the other spells differently. The rest are
# relocations that still appear in older seasons.
ABBREV_ALIASES = {
    'LA': 'LAR',     # nfl_data_py's Rams
    'WSH': 'WAS',    # ESPN's Commanders
    'JAC': 'JAX',    # occasional Jaguars variant
    'OAK': 'LV',     # Raiders, pre-2020
    'SD': 'LAC',     # Chargers, pre-2017
    'STL': 'LAR',    # Rams, pre-2016
}


def canonical_abbrev(abbrev):
    """Fold a source's abbreviation onto the one TEAM_ABBREV uses.

    Both scrape and grade must call this before building or comparing a game_id,
    or the same fixture gets two different IDs depending on which source saw it.
    """
    if not abbrev:
        return ''
    a = str(abbrev).strip().upper()
    return ABBREV_ALIASES.get(a, a)


def team_from_abbrev(abbrev, default=None):
    """Full team name for any abbreviation either source might send.

    Returns `default` (the raw abbreviation when not given) if it is genuinely
    unknown, so a new relocation shows up as an obviously wrong team name rather
    than silently grading nothing.
    """
    a = canonical_abbrev(abbrev)
    return ABBREV_TO_TEAM.get(a, default if default is not None else abbrev)


def make_game_id(season, week, away_abbrev, home_abbrev):
    """The one game_id format, shared by every source.

    nfl_data_py numbers weeks '01' and our ESPN code numbered them '1', so the
    same game carried two IDs and the ID match in do_grade never fired; grading
    fell through to a fallback that was itself inverted, so nothing matched at
    all.
    """
    return (f'{season}_{int(week):02d}_'
            f'{canonical_abbrev(away_abbrev)}_{canonical_abbrev(home_abbrev)}')


def canonical_game_id(gid):
    """Fold any stored or freshly built game_id onto one comparable form.

    Rows already in the database carry whatever their source spelled at the time
    - '2026_01_SF_LA' from nfl_data_py, '2025_1_DAL_PHI' from the ESPN path - so
    both sides have to be re-canonicalised before they can be compared.
    """
    if not gid:
        return ''
    parts = str(gid).split('_')
    if len(parts) != 4:
        return str(gid).strip().upper()
    season, week, away, home = parts
    try:
        return make_game_id(season, week, away, home)
    except (TypeError, ValueError):
        return str(gid).strip().upper()
