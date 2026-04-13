"""Score and rank pitcher recommendations."""

from typing import Optional
from app.models import PlayerInfo, ScheduledStart, GameOdds, PitcherRecommendation


def score_pitcher(
    pitcher: PlayerInfo,
    start: ScheduledStart,
    odds: Optional[GameOdds],
    all_pitchers: list[PlayerInfo],
    team_records: dict[str, dict] = None,
) -> PitcherRecommendation:
    """Calculate composite recommendation score (0-100).

    Uses Vegas lines if available, otherwise falls back to MLB team records
    for matchup strength signals.
    """
    breakdown = {}
    team_records = team_records or {}

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

    # Component 2: Win probability / matchup strength
    win_prob = None
    if odds:
        # Use Vegas implied probability
        if start.is_home:
            win_prob = odds.home_implied_prob
        else:
            win_prob = odds.away_implied_prob
        breakdown["matchup"] = win_prob if win_prob else 0.5
    elif team_records:
        # Fallback: use team win% as proxy for matchup strength
        pitcher_team = team_records.get(start.team_abbrev, {})
        opp_team = team_records.get(start.opponent_abbrev, {})
        pitcher_pct = pitcher_team.get("pct", 0.5)
        opp_pct = opp_team.get("pct", 0.5)
        # Pitcher on a better team facing a weaker opponent = better matchup
        # Simple model: pitcher_pct * (1 - opp_pct) * 2, clamped to [0, 1]
        matchup = pitcher_pct * (1 - opp_pct) * 2
        breakdown["matchup"] = max(0.0, min(1.0, matchup))
        win_prob = breakdown["matchup"]
    else:
        breakdown["matchup"] = 0.5

    # Component 3: Run environment
    if odds and odds.over_under:
        # Lower O/U = better for pitchers. Typical range: 6.5 to 11.5
        breakdown["run_env"] = max(0, min(1, (11.5 - odds.over_under) / 5.0))
    elif team_records:
        # Fallback: weaker opponent = likely lower-scoring game
        opp_pct = team_records.get(start.opponent_abbrev, {}).get("pct", 0.5)
        # Facing a losing team is favorable
        breakdown["run_env"] = max(0.0, min(1.0, 1.0 - opp_pct))
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

    # Component 6: Home advantage
    breakdown["home"] = 0.6 if start.is_home else 0.4

    # Weighted composite
    if odds:
        weights = {
            "projection": 0.25,
            "matchup": 0.25,
            "run_env": 0.15,
            "ownership": 0.10,
            "form": 0.15,
            "home": 0.10,
        }
    else:
        weights = {
            "projection": 0.30,
            "matchup": 0.20,
            "run_env": 0.15,
            "ownership": 0.10,
            "form": 0.15,
            "home": 0.10,
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
