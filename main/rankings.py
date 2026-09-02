"""Standard competition ranking — the one way this league orders people.

Tied players share the **best** rank, and the next distinct score resumes at its
positional index rather than the next integer. Three people tied at the top are
all 1st, and the fourth person is 4th, not 2nd:

    100, 100, 100, 90, 85   ->   1, 1, 1, 4, 5

This is "1224" ranking, the convention every real sports table uses. The site
previously numbered rows with `enumerate()` and `forloop.counter`, which hands
tied players different places for the same score and makes the leaderboard
disagree with itself — a player could sit 2nd on the home page and 3rd in their
own pick history off the same numbers.

Rank *changes* have to be computed from this too. With positional numbering, two
players tied all season showed a rank change every week as their arbitrary order
flipped.
"""


def competition_ranks(pairs):
    """``{key: rank}`` for ``(key, score)`` pairs, highest score first.

    Accepts any iterable of pairs, or a dict. Sorting happens here so callers
    cannot get it half right — sorted ascending, or sorted by a rounded value
    while ranking on the unrounded one.
    """
    if hasattr(pairs, 'items'):
        pairs = pairs.items()
    ordered = sorted(pairs, key=lambda kv: -kv[1])

    ranks = {}
    last_score = None
    last_rank = 0
    for position, (key, score) in enumerate(ordered, start=1):
        if score != last_score:
            last_rank, last_score = position, score
        ranks[key] = last_rank
    return ranks


def rank_rows(rows, score_key='score', name_key='username', rank_key='rank'):
    """Attach a competition rank to each row in place, and return them.

    Rows are expected pre-sorted by score descending — the rank is derived from
    the scores, not from the order, so a caller that sorts differently still gets
    correct ranks.
    """
    rows = list(rows)
    ranks = competition_ranks((r[name_key], r[score_key]) for r in rows)
    for row in rows:
        row[rank_key] = ranks[row[name_key]]
    return rows
