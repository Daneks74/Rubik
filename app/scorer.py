"""Score and rank pitcher recommendations."""

from typing import Optional
from app.models import PlayerInfo, ScheduledStart, GameOdds, PitcherRecommendation


def score_pitcher(
    pitcher: PlayerInfo,
    start: ScheduledStart,
    odds: Optional[GameOdds],
    all_pitchers: list[PlayerInfo],
) -> PitcherRecommendation:
    """Calculate composite recommendation score (0-100)."""
    breakdown = {}

    # Component 1: ESPN projected points (normalized across pool)
    proj_values = [p.projected_points for p in all_pitchers if p.projected_points > 0]
    if proj_values and pitcher.projected_points > 0:
        min_p, max_p = min(proj_values), max(proj_values)
        if max_p > min_p:
            breakdown["projection"] = (pitcher.projected_points - min_p) / (max_p - min_p)
        else:
            breakdown["projection"] = 0.5
    else:
        breakdown["projection"] = 0.5

    # Component 2: Win probability from Vegas
    win_prob = None
    if odds:
        if start.is_home:
            win_prob = odds.home_implied_prob
        else:
            win_prob = odds.away_implied_prob
    breakdown["win_prob"] = win_prob if win_prob else 0.5

    # Component 3: Run environment (lower O/U = better for pitchers)
    if odds and odds.over_under:
        ou = odds.over_under
        # Typical range: 6.5 to 11.5
        breakdown["run_env"] = max(0, min(1, (11.5 - ou) / 5.0))
    else:
        breakdown["run_env"] = 0.5

    # Component 4: Ownership % (higher = more consensus value)
    breakdown["ownership"] = min(1.0, pitcher.percent_owned / 50.0)

    # Component 5: Season form (total points normalized)
    pts_values = [p.total_points for p in all_pitchers if p.total_points > 0]
    if pts_values and pitcher.total_points > 0:
        min_t, max_t = min(pts_values), max(pts_values)
        if max_t > min_t:
            breakdown["form"] = (pitcher.total_points - min_t) / (max_t - min_t)
        else:
            breakdown["form"] = 0.5
    else:
        breakdown["form"] = 0.5

    # Weighted composite
    if odds:
        weights = {
            "projection": 0.30,
            "win_prob": 0.30,
            "run_env": 0.20,
            "ownership": 0.10,
            "form": 0.10,
        }
    else:
        weights = {
            "projection": 0.45,
            "win_prob": 0.0,
            "run_env": 0.0,
            "ownership": 0.20,
            "form": 0.35,
        }

    score = sum(breakdown[k] * weights[k] for k in weights) * 100

    return PitcherRecommendation(
        pitcher=pitcher,
        scheduled_start=start,
        odds=odds,
        win_probability=win_prob,
        score=round(score, 1),
        score_breakdown={k: round(v, 3) for k, v in breakdown.items()},
    )


def rank_recommendations(recs: list[PitcherRecommendation]) -> list[PitcherRecommendation]:
    """Sort by score descending."""
    return sorted(recs, key=lambda r: r.score, reverse=True)
