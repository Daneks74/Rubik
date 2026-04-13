from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class PlayerInfo:
    """A player from ESPN fantasy league."""
    espn_id: int
    name: str
    pro_team: str
    position: str
    eligible_slots: list[str] = field(default_factory=list)
    lineup_slot: str = ""
    percent_owned: float = 0.0
    percent_started: float = 0.0
    injury_status: str = "ACTIVE"
    stats: dict = field(default_factory=dict)
    projected_stats: dict = field(default_factory=dict)
    total_points: float = 0.0
    projected_points: float = 0.0


@dataclass
class ScheduledStart:
    """A probable pitcher start from MLB Stats API."""
    pitcher_name: str
    team_name: str
    team_abbrev: str
    opponent_name: str
    opponent_abbrev: str
    game_date: date
    game_id: int
    is_home: bool


@dataclass
class GameOdds:
    """Vegas odds for a single game."""
    home_team: str
    away_team: str
    commence_time: str
    home_moneyline: Optional[int] = None
    away_moneyline: Optional[int] = None
    over_under: Optional[float] = None
    home_implied_prob: Optional[float] = None
    away_implied_prob: Optional[float] = None


@dataclass
class PitcherRecommendation:
    """Scored pitcher recommendation combining all data sources."""
    pitcher: PlayerInfo
    scheduled_start: ScheduledStart
    odds: Optional[GameOdds] = None
    win_probability: Optional[float] = None
    score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)


@dataclass
class TeamRanking:
    """A team's ranking data across categories."""
    team_name: str
    team_abbrev: str
    team_id: int
    logo_url: str = ""
    category_values: dict = field(default_factory=dict)
    category_ranks: dict = field(default_factory=dict)
    overall_rank: int = 0
    total_rank_score: float = 0.0


@dataclass
class ProjectionData:
    """Player projections from a single source."""
    source: str
    stats: dict = field(default_factory=dict)
