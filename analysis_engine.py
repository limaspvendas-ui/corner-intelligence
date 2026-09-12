import math
from statistics import mean


def safe_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def average(values):
    clean = [safe_float(v) for v in values]
    clean = [v for v in clean if v is not None]
    return mean(clean) if clean else None


def expected_total_goals(home_scoring_avg, away_conceding_avg, away_scoring_avg, home_conceding_avg):
    values = [
        safe_float(home_scoring_avg),
        safe_float(away_conceding_avg),
        safe_float(away_scoring_avg),
        safe_float(home_conceding_avg),
    ]
    if any(v is None for v in values):
        return None
    return sum(values) / 2.0


def poisson_pmf(lam, k):
    if lam is None or lam < 0 or k < 0:
        return None
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def poisson_at_least(lam, k):
    if lam is None or lam < 0 or k < 0:
        return None
    return 1.0 - sum(poisson_pmf(lam, i) for i in range(k))


def goals_probabilities(lam):
    if lam is None:
        return None
    return {
        "over_0_5": poisson_at_least(lam, 1),
        "over_1_5": poisson_at_least(lam, 2),
        "over_2_5": poisson_at_least(lam, 3),
        "over_3_5": poisson_at_least(lam, 4),
        "over_4_5": poisson_at_least(lam, 5),
    }


def expected_corners(home_corners_for, away_corners_against, away_corners_for, home_corners_against):
    values = [
        safe_float(home_corners_for),
        safe_float(away_corners_against),
        safe_float(away_corners_for),
        safe_float(home_corners_against),
    ]
    if any(v is None for v in values):
        return None
    return ((values[0] + values[1]) / 2.0) + ((values[2] + values[3]) / 2.0)
