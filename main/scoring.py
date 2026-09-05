"""The points formula, in one place.

It was copy-pasted into auto.py, views.py and the strategy simulator, so a fix
to any one of them fixed nothing. Every caller imports it from here.
"""


def calculate_points(underdog_ml, favorite_ml):
    """Points for backing the underdog, from the two moneylines.

    Reduces to sqrt(|ml_a| * |ml_b|) / 100 - the geometric mean of the two lines -
    so it is symmetric: the parameter names say underdog/favorite, but passing
    them the other way round gives the same answer. A missing line on either side
    is worth a flat 1.0, the same as the favorite.
    """
    u = abs(float(underdog_ml))
    f = abs(float(favorite_ml))
    if u == 0 or f == 0:
        return 1.0
    u_ratio = u / 100
    f_ratio = 100 / f
    hp = ((1 / (u_ratio * f_ratio)) ** 0.5) - 1
    return round((hp + 1) * u_ratio, 2)
