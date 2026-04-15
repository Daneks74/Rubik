"""
Demo projection builder — generates realistic-looking MLB pitcher projections
from hardcoded starter data and deterministic formulas.

Designed for easy replacement: swap each build_* function with a real data
source later without changing the rest of the pipeline.
"""
import logging
from dataclasses import dataclass, field
from datetime import date

logger = logging.getLogger(__name__)

MODEL_VERSION = "demo-v1"

# ── Demo slate: realistic pitcher/matchup combos ──

_DEMO_SLATE = [
    {"name": "Corbin Burnes",   "team": "Ari", "opp": "LAD", "ha": "home", "throws": "R"},
    {"name": "Chris Sale",      "team": "Atl", "opp": "NYM", "ha": "away", "throws": "L"},
    {"name": "Gerrit Cole",     "team": "NYY", "opp": "Bos", "ha": "home", "throws": "R"},
    {"name": "Zack Wheeler",    "team": "Phi", "opp": "Wsh", "ha": "home", "throws": "R"},
    {"name": "Logan Webb",      "team": "SF",  "opp": "Col", "ha": "away", "throws": "R"},
    {"name": "Tarik Skubal",    "team": "Det", "opp": "CLE", "ha": "home", "throws": "L"},
    {"name": "Dylan Cease",     "team": "SD",  "opp": "Cin", "ha": "away", "throws": "R"},
    {"name": "Pablo Lopez",     "team": "Min", "opp": "ChW", "ha": "home", "throws": "R"},
]

# Baseline profiles keyed by pitcher name — each is a realistic ROS projection
_DEMO_BASELINES: dict[str, dict] = {
    "Corbin Burnes":  {"ip": 6.2, "k_pct": 26.5, "bb_pct": 5.1, "era": 3.15, "whip": 1.08, "xera": 3.02, "xwoba": 0.285},
    "Chris Sale":     {"ip": 5.8, "k_pct": 28.9, "bb_pct": 6.3, "era": 3.40, "whip": 1.12, "xera": 3.25, "xwoba": 0.290},
    "Gerrit Cole":    {"ip": 6.4, "k_pct": 30.1, "bb_pct": 5.5, "era": 2.95, "whip": 1.03, "xera": 2.88, "xwoba": 0.272},
    "Zack Wheeler":   {"ip": 6.5, "k_pct": 25.8, "bb_pct": 5.0, "era": 3.05, "whip": 1.05, "xera": 2.95, "xwoba": 0.278},
    "Logan Webb":     {"ip": 6.3, "k_pct": 21.2, "bb_pct": 5.8, "era": 3.35, "whip": 1.15, "xera": 3.28, "xwoba": 0.295},
    "Tarik Skubal":   {"ip": 6.1, "k_pct": 29.5, "bb_pct": 5.2, "era": 2.80, "whip": 0.98, "xera": 2.70, "xwoba": 0.265},
    "Dylan Cease":    {"ip": 5.7, "k_pct": 27.3, "bb_pct": 8.5, "era": 3.70, "whip": 1.25, "xera": 3.55, "xwoba": 0.305},
    "Pablo Lopez":    {"ip": 5.9, "k_pct": 24.0, "bb_pct": 5.9, "era": 3.55, "whip": 1.18, "xera": 3.45, "xwoba": 0.298},
}


# ── Data classes for pipeline output ──

@dataclass
class StarterRecord:
    game_date: date
    game_id: str
    pitcher_name: str
    pitcher_team: str
    opponent_team: str
    home_away: str
    throws: str
    status: str = "probable"
    source: str = "demo"


@dataclass
class BaselineRecord:
    pitcher_name: str
    pitcher_team: str
    season_year: int
    ros_ip_per_start: float
    ros_k_pct: float
    ros_bb_pct: float
    ros_era: float
    ros_whip: float
    xera: float
    xwoba: float


@dataclass
class ProjectionRecord:
    game_date: date
    game_id: str
    pitcher_name: str
    pitcher_team: str
    opponent_team: str
    projected_ip: float
    projected_k: float
    projected_bb: float
    projected_h: float
    projected_er: float
    projected_era: float
    projected_whip: float
    win_probability: float
    blowup_probability: float
    confidence: str
    stream_score: float
    model_version: str = field(default=MODEL_VERSION)


# ── Builder functions ──

def build_demo_probable_starters(target_date: date) -> list[StarterRecord]:
    """Generate a slate of 8 demo probable starters for the given date."""
    starters = []
    for i, s in enumerate(_DEMO_SLATE, start=1):
        starters.append(StarterRecord(
            game_date=target_date,
            game_id=f"mlb-{target_date.isoformat()}-{i:03d}",
            pitcher_name=s["name"],
            pitcher_team=s["team"],
            opponent_team=s["opp"],
            home_away=s["ha"],
            throws=s["throws"],
        ))
    logger.info("Built %d demo starters for %s", len(starters), target_date)
    return starters


def build_demo_baselines(starters: list[StarterRecord], season_year: int) -> list[BaselineRecord]:
    """Generate baseline records for each starter from hardcoded profiles."""
    baselines = []
    for s in starters:
        profile = _DEMO_BASELINES.get(s.pitcher_name)
        if not profile:
            continue
        baselines.append(BaselineRecord(
            pitcher_name=s.pitcher_name,
            pitcher_team=s.pitcher_team,
            season_year=season_year,
            ros_ip_per_start=profile["ip"],
            ros_k_pct=profile["k_pct"],
            ros_bb_pct=profile["bb_pct"],
            ros_era=profile["era"],
            ros_whip=profile["whip"],
            xera=profile["xera"],
            xwoba=profile["xwoba"],
        ))
    logger.info("Built %d demo baselines for %d", len(baselines), season_year)
    return baselines


def build_demo_daily_projections(
    starters: list[StarterRecord],
    baselines: list[BaselineRecord],
    model_version: str = MODEL_VERSION,
) -> list[ProjectionRecord]:
    """
    Build daily projections from baselines using simple deterministic formulas.

    Key relationships:
    - projected_ip ≈ baseline ros_ip_per_start (slight variance by home/away)
    - projected_k = ip * (k_pct / 100) * batters_per_ip_factor
    - projected_bb = ip * (bb_pct / 100) * batters_per_ip_factor
    - projected_h = ip * whip - projected_bb
    - projected_er = ip * era / 9
    - win_probability rewards low ERA, high IP, home advantage
    - blowup_probability rewards high ERA/WHIP
    - stream_score combines everything into a 0-100 score
    """
    baseline_map = {b.pitcher_name: b for b in baselines}
    projections = []

    for s in starters:
        bl = baseline_map.get(s.pitcher_name)
        if not bl:
            continue

        # IP: home pitchers go slightly deeper
        home_bonus = 0.15 if s.home_away == "home" else 0.0
        proj_ip = round(bl.ros_ip_per_start + home_bonus, 1)

        # Batters faced per IP (league average ~4.3 BF/IP)
        bf_per_ip = 4.3

        # K, BB derived from rates and batters faced
        batters_faced = proj_ip * bf_per_ip
        proj_k = round(batters_faced * (bl.ros_k_pct / 100), 1)
        proj_bb = round(batters_faced * (bl.ros_bb_pct / 100), 1)

        # H derived from WHIP: WHIP = (H + BB) / IP → H = WHIP * IP - BB
        proj_h = round(bl.ros_whip * proj_ip - proj_bb, 1)
        proj_h = max(proj_h, 1.0)  # floor at 1 hit

        # ER from ERA: ERA = ER * 9 / IP → ER = ERA * IP / 9
        proj_er = round(bl.ros_era * proj_ip / 9.0, 1)

        # Daily ERA/WHIP just reflect the projected line
        proj_era = round(proj_er * 9.0 / proj_ip, 2) if proj_ip > 0 else 0.0
        proj_whip = round((proj_h + proj_bb) / proj_ip, 2) if proj_ip > 0 else 0.0

        # Win probability: base 0.45, boosted by low ERA, high IP, home field
        era_factor = max(0, (4.50 - bl.ros_era) / 4.50) * 0.15   # up to +0.15 for elite ERA
        ip_factor = max(0, (proj_ip - 5.0) / 3.0) * 0.08          # longer outings help
        home_factor = 0.03 if s.home_away == "home" else 0.0
        win_prob = round(min(0.70, 0.38 + era_factor + ip_factor + home_factor), 2)

        # Blowup probability: higher ERA/WHIP → more risk
        era_risk = max(0, (bl.ros_era - 2.50) / 5.0) * 0.20
        whip_risk = max(0, (bl.ros_whip - 0.90) / 0.60) * 0.15
        blowup = round(min(0.50, 0.05 + era_risk + whip_risk), 2)

        # Confidence: based on how tight the baseline metrics are
        xera_gap = abs(bl.ros_era - bl.xera)
        if xera_gap < 0.15 and bl.ros_whip < 1.15:
            confidence = "high"
        elif xera_gap < 0.30 or bl.ros_whip < 1.25:
            confidence = "medium"
        else:
            confidence = "low"

        # Stream score (0-100): rewards K, win prob; penalizes ERA, blowup
        k_score = min(30, proj_k * 3.5)                          # up to 30 pts
        win_score = win_prob * 30                                  # up to 21 pts
        era_score = max(0, (5.00 - proj_era) / 5.00) * 25         # up to 25 pts
        blowup_penalty = blowup * 30                               # up to 15 pts off
        stream = round(min(100, max(0, k_score + win_score + era_score - blowup_penalty)), 1)

        projections.append(ProjectionRecord(
            game_date=s.game_date,
            game_id=s.game_id,
            pitcher_name=s.pitcher_name,
            pitcher_team=s.pitcher_team,
            opponent_team=s.opponent_team,
            projected_ip=proj_ip,
            projected_k=proj_k,
            projected_bb=proj_bb,
            projected_h=proj_h,
            projected_er=proj_er,
            projected_era=proj_era,
            projected_whip=proj_whip,
            win_probability=win_prob,
            blowup_probability=blowup,
            confidence=confidence,
            stream_score=stream,
            model_version=model_version,
        ))

    # Sort by stream_score descending
    projections.sort(key=lambda p: p.stream_score, reverse=True)
    logger.info("Built %d demo projections (model=%s)", len(projections), model_version)
    return projections
