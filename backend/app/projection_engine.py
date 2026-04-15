"""
Formula-based projection engine — produces daily pitcher projections from
stored baselines, lightweight team context, and matchup heuristics.

Designed for modularity: matchup context, projection formulas, and scoring
are all isolated so they can be upgraded independently with opponent lineups,
park factors, bullpen quality, and simulation later.

Model version: formula-v3-real-team-context
"""
import logging
from dataclasses import dataclass

from app.projection_builder import BaselineRecord, ProjectionRecord, StarterRecord
from app.team_context import NEUTRAL_TEAM_CTX

logger = logging.getLogger(__name__)

MODEL_VERSION = "formula-v3-real-team-context"

# ── MLB league-average defaults for missing baseline fields ──
# Used when a pitcher's baseline is incomplete. These are roughly
# 2024-2025 MLB starter averages.

DEFAULTS = {
    "ros_ip_per_start": 5.5,
    "ros_k_pct": 22.0,      # ~22% K rate
    "ros_bb_pct": 7.5,      # ~7.5% BB rate
    "ros_era": 4.20,
    "ros_whip": 1.28,
    "xera": None,            # leave unknown
    "xwoba": None,           # leave unknown
}

# Batters faced per IP — league-wide average (~4.3)
BF_PER_IP = 4.3


# ── Matchup context ──

@dataclass
class MatchupContext:
    """Matchup factors applied to the baseline projection.
    Populated from team context data and home/away environment.
    All multipliers default to neutral (1.0)."""

    is_home: bool
    # IP adjustment: home pitchers tend to go slightly deeper
    ip_adjustment: float = 0.0
    # Opponent difficulty: >1.0 = tougher lineup, <1.0 = weaker lineup
    opp_k_factor: float = 1.0    # multiplier on K rate
    opp_bb_factor: float = 1.0   # multiplier on BB rate
    opp_hit_factor: float = 1.0  # multiplier on hits allowed
    opp_era_factor: float = 1.0  # multiplier on ER
    # Park / run environment: >1.0 = hitter-friendly, <1.0 = pitcher-friendly
    park_factor: float = 1.0
    # Team win environment: base win rate for the pitcher's team
    team_win_rate: float = 0.500
    # Own-team bullpen quality: >1.0 = strong pen → better win hold
    bullpen_factor: float = 1.0
    # Run environment factor for blowup risk
    run_env_factor: float = 1.0
    # How many baseline fields used defaults (for confidence tracking)
    defaults_used: int = 0


def build_matchup_context(
    starter: StarterRecord,
    own_team_ctx: dict | None = None,
    opp_team_ctx: dict | None = None,
) -> MatchupContext:
    """Build a matchup context from a starter record and team context.

    Uses team-level heuristics to populate opponent difficulty multipliers,
    park/run-environment factors, and win-probability inputs.
    Falls back to neutral (1.0) defaults when team context is unavailable.
    """
    is_home = starter.home_away == "home"
    own = own_team_ctx or NEUTRAL_TEAM_CTX
    opp = opp_team_ctx or NEUTRAL_TEAM_CTX

    # ── IP adjustment ──
    # Home pitchers go ~0.2 IP deeper on average.
    # Stronger opposing offense knocks pitchers out slightly earlier.
    ip_adj = 0.2 if is_home else 0.0
    ip_adj += (1.0 - opp["offense_strength"]) * 0.3
    ip_adj = max(-0.3, min(0.4, ip_adj))

    # ── Opponent K factor ──
    # Teams that strike out more are easier to K against.
    opp_k_factor = opp["offense_k_tendency"]

    # ── Opponent hit factor ──
    # Stronger offenses produce more hits. Dampened to 50% of raw factor.
    opp_hit_factor = 1.0 + (opp["offense_strength"] - 1.0) * 0.5

    # ── Opponent ERA factor ──
    # Stronger offenses drive more earned runs. Dampened to 60%.
    opp_era_factor = 1.0 + (opp["offense_strength"] - 1.0) * 0.6

    # ── Park / run environment ──
    # Based on game location: pitcher's home park or opponent's park.
    if is_home:
        park_factor = own["run_environment_factor"]
        run_env_factor = own["run_environment_factor"]
    else:
        park_factor = opp["run_environment_factor"]
        run_env_factor = opp["run_environment_factor"]

    # ── Team win rate ──
    # Base 0.50, adjusted by home/away, own team run support, opponent offense.
    home_edge = 0.02 if is_home else -0.02
    own_support = (own["win_support_factor"] - 1.0) * 0.5
    opp_penalty = (opp["offense_strength"] - 1.0) * 0.3
    team_win_rate = max(0.35, min(0.65, 0.50 + home_edge + own_support - opp_penalty))

    # ── Bullpen factor ──
    bullpen_factor = own["bullpen_support_factor"]

    return MatchupContext(
        is_home=is_home,
        ip_adjustment=round(ip_adj, 3),
        opp_k_factor=round(opp_k_factor, 3),
        opp_hit_factor=round(opp_hit_factor, 3),
        opp_era_factor=round(opp_era_factor, 3),
        park_factor=round(park_factor, 3),
        team_win_rate=round(team_win_rate, 3),
        bullpen_factor=round(bullpen_factor, 3),
        run_env_factor=round(run_env_factor, 3),
    )


# ── Baseline resolution ──

def _resolve_baseline(bl: BaselineRecord) -> tuple[dict, int]:
    """Resolve a baseline, filling in league-average defaults for any missing
    fields. Returns (resolved_dict, defaults_used_count)."""
    defaults_used = 0

    def _get(field: str) -> float:
        nonlocal defaults_used
        val = getattr(bl, field, None)
        if val is not None and val > 0:
            return val
        default = DEFAULTS.get(field)
        if default is not None:
            defaults_used += 1
            return default
        return 0.0

    resolved = {
        "ip": _get("ros_ip_per_start"),
        "k_pct": _get("ros_k_pct"),
        "bb_pct": _get("ros_bb_pct"),
        "era": _get("ros_era"),
        "whip": _get("ros_whip"),
        "xera": bl.xera,   # None is OK
        "xwoba": bl.xwoba,  # None is OK
    }
    return resolved, defaults_used


# ── Core projection formula ──

def project_pitcher_line(
    starter: StarterRecord,
    baseline: BaselineRecord,
    ctx: MatchupContext,
    model_version: str = MODEL_VERSION,
) -> tuple[ProjectionRecord, dict]:
    """Project a single pitcher's daily line from baseline + matchup context.

    Returns (ProjectionRecord, debug_dict) where debug_dict contains the
    intermediate values used in the calculation.
    """
    bl, defaults_used = _resolve_baseline(baseline)
    ctx.defaults_used = defaults_used

    # ── Innings pitched ──
    proj_ip = round(bl["ip"] + ctx.ip_adjustment, 1)
    proj_ip = max(proj_ip, 3.0)  # floor: even bad starters get 3 IP projected

    # ── Batters faced ──
    bf = proj_ip * BF_PER_IP

    # ── Strikeouts ──
    # K rate adjusted by opponent K tendency (high-K lineups are easier to K)
    adj_k_pct = bl["k_pct"] * ctx.opp_k_factor
    proj_k = round(bf * (adj_k_pct / 100), 1)

    # ── Walks ──
    adj_bb_pct = bl["bb_pct"] * ctx.opp_bb_factor
    proj_bb = round(bf * (adj_bb_pct / 100), 1)

    # ── Hits ──
    # WHIP = (H + BB) / IP → H = WHIP * IP - BB, adjusted by opp and park
    raw_h = bl["whip"] * proj_ip - proj_bb
    proj_h = round(max(1.0, raw_h * ctx.opp_hit_factor * ctx.park_factor), 1)

    # ── Earned runs ──
    # Blend ERA with xERA when available for a more stable ER estimate
    effective_era = bl["era"]
    if bl["xera"] is not None:
        # 60/40 blend: weight xERA slightly less since it's more volatile early
        effective_era = bl["era"] * 0.6 + bl["xera"] * 0.4

    raw_er = effective_era * proj_ip / 9.0
    proj_er = round(max(0, raw_er * ctx.opp_era_factor * ctx.park_factor), 1)

    # ── Derived rate stats ──
    proj_era = round(proj_er * 9.0 / proj_ip, 2) if proj_ip > 0 else 0.0
    proj_whip = round((proj_h + proj_bb) / proj_ip, 2) if proj_ip > 0 else 0.0

    # ── Win probability ──
    # Factors: pitcher quality (ERA), team environment, innings depth,
    # home field, bullpen quality
    era_edge = max(-0.12, min(0.12, (4.20 - effective_era) / 4.20 * 0.12))
    ip_edge = max(0, (proj_ip - 5.0) / 5.0) * 0.06
    whip_edge = max(-0.06, min(0.06, (1.28 - bl["whip"]) / 1.28 * 0.06))
    bullpen_edge = (ctx.bullpen_factor - 1.0) * 0.35
    win_prob = round(max(0.15, min(0.70,
        ctx.team_win_rate + era_edge + ip_edge + whip_edge + bullpen_edge
    )), 2)

    # ── Blowup probability ──
    # "Blowup" = 5+ ER outing. Higher ERA/WHIP, shorter IP, stronger
    # opponent, and hitter-friendly environment all increase risk.
    era_risk = max(0, (effective_era - 3.00) / 4.00) * 0.18
    whip_risk = max(0, (bl["whip"] - 1.00) / 0.50) * 0.12
    ip_risk = max(0, (5.5 - proj_ip) / 3.0) * 0.08
    env_risk = (ctx.run_env_factor - 1.0) * 0.10
    opp_risk = max(0, (ctx.opp_era_factor - 1.0)) * 0.06
    blowup = round(max(0.03, min(0.50,
        0.06 + era_risk + whip_risk + ip_risk + env_risk + opp_risk
    )), 2)

    # ── Confidence ──
    if defaults_used == 0 and bl["xera"] is not None:
        confidence = "high"
    elif defaults_used <= 1:
        confidence = "medium"
    else:
        confidence = "low"

    # ── Stream score (0-100) ──
    # Composite score for fantasy streaming: rewards K, win prob, low ERA;
    # penalizes blowup risk, high WHIP.
    k_pts = min(28, proj_k * 3.2)                             # 0-28: raw K value
    win_pts = win_prob * 25                                     # 0-17.5: win upside
    era_pts = max(0, (5.50 - proj_era) / 5.50) * 22            # 0-22: ERA quality
    whip_pts = max(0, (1.60 - proj_whip) / 1.60) * 12          # 0-12: WHIP quality
    ip_pts = max(0, min(8, (proj_ip - 4.0) * 2.0))             # 0-8: length bonus
    blowup_pen = blowup * 28                                    # 0-14: blowup penalty
    stream = round(max(0, min(100, k_pts + win_pts + era_pts + whip_pts + ip_pts - blowup_pen)), 1)

    projection = ProjectionRecord(
        game_date=starter.game_date,
        game_id=starter.game_id,
        pitcher_name=starter.pitcher_name,
        pitcher_team=starter.pitcher_team,
        opponent_team=starter.opponent_team,
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
    )

    debug = {
        "baseline_inputs": {
            "ros_ip_per_start": bl["ip"],
            "ros_k_pct": bl["k_pct"],
            "ros_bb_pct": bl["bb_pct"],
            "ros_era": bl["era"],
            "ros_whip": bl["whip"],
            "xera": bl["xera"],
            "xwoba": bl["xwoba"],
        },
        "matchup_context": {
            "is_home": ctx.is_home,
            "ip_adjustment": ctx.ip_adjustment,
            "opp_k_factor": ctx.opp_k_factor,
            "opp_hit_factor": ctx.opp_hit_factor,
            "opp_era_factor": ctx.opp_era_factor,
            "park_factor": ctx.park_factor,
            "team_win_rate": ctx.team_win_rate,
            "bullpen_factor": ctx.bullpen_factor,
            "run_env_factor": ctx.run_env_factor,
        },
        "intermediate": {
            "effective_era": round(effective_era, 3),
            "batters_faced": round(bf, 1),
            "defaults_used": defaults_used,
            "bullpen_edge": round(bullpen_edge, 4),
            "env_risk": round(env_risk, 4),
            "opp_risk": round(opp_risk, 4),
        },
        "score_breakdown": {
            "k_pts": round(k_pts, 1),
            "win_pts": round(win_pts, 1),
            "era_pts": round(era_pts, 1),
            "whip_pts": round(whip_pts, 1),
            "ip_pts": round(ip_pts, 1),
            "blowup_penalty": round(blowup_pen, 1),
        },
    }

    return projection, debug


# ── Batch projection builder ──

def build_daily_projections_for_starters(
    starters: list[StarterRecord],
    baselines: list[BaselineRecord],
    team_context_map: dict[str, dict] | None = None,
    model_version: str = MODEL_VERSION,
) -> tuple[list[ProjectionRecord], list[dict]]:
    """Build projections for all starters that have a matching baseline.

    Args:
        starters: probable starters for the day
        baselines: pitcher baseline records
        team_context_map: optional dict keyed by team_code with context factors
        model_version: model version string

    Returns:
        (projections sorted by stream_score desc, debug_info list)
    """
    baseline_map = {bl.pitcher_name: bl for bl in baselines}
    tc_map = team_context_map or {}

    projections: list[ProjectionRecord] = []
    debug_list: list[dict] = []
    matched = 0
    defaults_total = 0

    for s in starters:
        bl = baseline_map.get(s.pitcher_name)
        if not bl:
            logger.warning("No baseline for %s — skipping projection", s.pitcher_name)
            continue

        matched += 1

        # Look up team context for own team and opponent
        own_ctx = tc_map.get(s.pitcher_team)
        opp_ctx = tc_map.get(s.opponent_team)

        ctx = build_matchup_context(s, own_ctx, opp_ctx)
        proj, debug = project_pitcher_line(s, bl, ctx, model_version)

        # Add team context info to debug output
        debug["team_context"] = {
            "own_team": own_ctx,
            "opp_team": opp_ctx,
        }

        defaults_total += ctx.defaults_used
        projections.append(proj)
        debug_list.append({
            "pitcher_name": s.pitcher_name,
            "pitcher_team": s.pitcher_team,
            "opponent_team": s.opponent_team,
            "home_away": s.home_away,
            **debug,
            "projection": {
                "projected_ip": proj.projected_ip,
                "projected_k": proj.projected_k,
                "projected_bb": proj.projected_bb,
                "projected_h": proj.projected_h,
                "projected_er": proj.projected_er,
                "projected_era": proj.projected_era,
                "projected_whip": proj.projected_whip,
                "win_probability": proj.win_probability,
                "blowup_probability": proj.blowup_probability,
                "confidence": proj.confidence,
                "stream_score": proj.stream_score,
            },
        })

    projections.sort(key=lambda p: p.stream_score, reverse=True)

    team_ctx_status = f"{len(tc_map)} teams" if tc_map else "none (neutral)"
    logger.info(
        "Projections built: %d/%d starters matched, %d total defaults used, "
        "team_context=%s, model=%s",
        matched, len(starters), defaults_total, team_ctx_status, model_version,
    )
    return projections, debug_list
