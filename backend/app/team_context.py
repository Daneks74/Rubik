"""
Team-context layer — provides team-level metrics for the projection engine.

Supports real current-season metrics from the MLB Stats API with graceful
fallback to static defaults when API data is unavailable.

Each team has five factors centered on 1.0 (neutral):
  - offense_strength:      stronger offense -> harder on opposing pitchers
  - offense_k_tendency:    higher -> lineup strikes out more (easier to K)
  - win_support_factor:    higher -> better run support for own pitchers
  - bullpen_support_factor: higher -> better chance of holding leads
  - run_environment_factor: higher -> more run-scoring environment (park + context)

Data source: MLB Stats API team-level season stats (hitting + pitching).
Normalization: each metric computed relative to league average, clamped [0.85, 1.15].
"""
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.http_client import resilient_get
from app.models import TeamContext

logger = logging.getLogger(__name__)

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

# ── MLB API abbreviation -> internal team code ──
# Matches the normalization used by probable_starters.py.
_ABBREV_MAP: dict[str, str] = {
    "AZ": "Ari", "ARI": "Ari",
    "WSH": "Wsh",
    "CWS": "ChW",
    "CHC": "ChC",
    "KC": "KC",
    "SD": "SD",
    "SF": "SF",
    "TB": "TB",
    "STL": "StL",
    "NYY": "NYY",
    "NYM": "NYM",
    "LAD": "LAD",
    "LAA": "LAA",
}


def _normalize_abbrev(abbrev: str) -> str:
    """Normalize an MLB API team abbreviation to internal format."""
    upper = abbrev.strip().upper()
    if upper in _ABBREV_MAP:
        return _ABBREV_MAP[upper]
    return abbrev.strip().capitalize() if len(abbrev) <= 3 else abbrev.strip()


def _safe_float(val, default: float = 0.0) -> float:
    """Convert a value to float, returning default on failure."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float = 0.85, hi: float = 1.15) -> float:
    """Clamp a factor to bounded range to prevent extreme projection swings."""
    return max(lo, min(hi, value))


# ── Default team context (static fallback) ──
# Values centered on 1.0 (neutral). Differentiated by rough 2024-2025 team
# profiles. Used when real API data is unavailable.

_DEFAULT_TEAMS: dict[str, dict[str, float]] = {
    # -- AL East --
    "NYY": {"offense_strength": 1.07, "offense_k_tendency": 0.96, "win_support_factor": 1.06, "bullpen_support_factor": 1.03, "run_environment_factor": 1.00},
    "Bal": {"offense_strength": 1.04, "offense_k_tendency": 0.99, "win_support_factor": 1.03, "bullpen_support_factor": 1.02, "run_environment_factor": 0.99},
    "Bos": {"offense_strength": 1.01, "offense_k_tendency": 0.98, "win_support_factor": 1.00, "bullpen_support_factor": 0.99, "run_environment_factor": 1.03},
    "TB":  {"offense_strength": 0.98, "offense_k_tendency": 1.01, "win_support_factor": 0.99, "bullpen_support_factor": 1.02, "run_environment_factor": 0.97},
    "Tor": {"offense_strength": 0.99, "offense_k_tendency": 1.00, "win_support_factor": 0.98, "bullpen_support_factor": 0.98, "run_environment_factor": 1.00},
    # -- AL Central --
    "Cle": {"offense_strength": 1.02, "offense_k_tendency": 0.97, "win_support_factor": 1.03, "bullpen_support_factor": 1.06, "run_environment_factor": 0.96},
    "Min": {"offense_strength": 1.03, "offense_k_tendency": 1.00, "win_support_factor": 1.02, "bullpen_support_factor": 0.99, "run_environment_factor": 1.00},
    "Det": {"offense_strength": 0.94, "offense_k_tendency": 1.06, "win_support_factor": 0.95, "bullpen_support_factor": 0.96, "run_environment_factor": 0.97},
    "KC":  {"offense_strength": 0.98, "offense_k_tendency": 1.02, "win_support_factor": 0.98, "bullpen_support_factor": 0.99, "run_environment_factor": 1.00},
    "ChW": {"offense_strength": 0.90, "offense_k_tendency": 1.08, "win_support_factor": 0.91, "bullpen_support_factor": 0.92, "run_environment_factor": 0.96},
    # -- AL West --
    "Hou": {"offense_strength": 1.06, "offense_k_tendency": 0.96, "win_support_factor": 1.05, "bullpen_support_factor": 1.04, "run_environment_factor": 1.00},
    "Sea": {"offense_strength": 1.00, "offense_k_tendency": 1.02, "win_support_factor": 1.01, "bullpen_support_factor": 1.02, "run_environment_factor": 0.97},
    "Tex": {"offense_strength": 1.03, "offense_k_tendency": 0.99, "win_support_factor": 1.02, "bullpen_support_factor": 0.99, "run_environment_factor": 1.05},
    "LAA": {"offense_strength": 0.93, "offense_k_tendency": 1.04, "win_support_factor": 0.93, "bullpen_support_factor": 0.93, "run_environment_factor": 0.98},
    "Oak": {"offense_strength": 0.91, "offense_k_tendency": 1.07, "win_support_factor": 0.91, "bullpen_support_factor": 0.91, "run_environment_factor": 0.96},
    # -- NL East --
    "Atl": {"offense_strength": 1.06, "offense_k_tendency": 0.96, "win_support_factor": 1.06, "bullpen_support_factor": 1.05, "run_environment_factor": 1.02},
    "Phi": {"offense_strength": 1.05, "offense_k_tendency": 0.97, "win_support_factor": 1.05, "bullpen_support_factor": 1.04, "run_environment_factor": 1.04},
    "NYM": {"offense_strength": 1.00, "offense_k_tendency": 0.97, "win_support_factor": 0.99, "bullpen_support_factor": 0.99, "run_environment_factor": 0.96},
    "Wsh": {"offense_strength": 0.93, "offense_k_tendency": 1.03, "win_support_factor": 0.94, "bullpen_support_factor": 0.95, "run_environment_factor": 0.97},
    "Mia": {"offense_strength": 0.92, "offense_k_tendency": 1.05, "win_support_factor": 0.93, "bullpen_support_factor": 0.96, "run_environment_factor": 0.96},
    # -- NL Central --
    "Mil": {"offense_strength": 1.01, "offense_k_tendency": 0.98, "win_support_factor": 1.01, "bullpen_support_factor": 1.04, "run_environment_factor": 1.01},
    "ChC": {"offense_strength": 1.00, "offense_k_tendency": 1.00, "win_support_factor": 1.00, "bullpen_support_factor": 0.99, "run_environment_factor": 1.02},
    "StL": {"offense_strength": 0.97, "offense_k_tendency": 1.00, "win_support_factor": 0.97, "bullpen_support_factor": 0.97, "run_environment_factor": 0.97},
    "Pit": {"offense_strength": 0.95, "offense_k_tendency": 1.03, "win_support_factor": 0.95, "bullpen_support_factor": 0.96, "run_environment_factor": 0.96},
    "Cin": {"offense_strength": 1.00, "offense_k_tendency": 1.01, "win_support_factor": 0.99, "bullpen_support_factor": 0.97, "run_environment_factor": 1.06},
    # -- NL West --
    "LAD": {"offense_strength": 1.08, "offense_k_tendency": 0.95, "win_support_factor": 1.07, "bullpen_support_factor": 1.05, "run_environment_factor": 1.00},
    "SD":  {"offense_strength": 1.03, "offense_k_tendency": 0.98, "win_support_factor": 1.02, "bullpen_support_factor": 1.03, "run_environment_factor": 0.98},
    "Ari": {"offense_strength": 0.97, "offense_k_tendency": 1.02, "win_support_factor": 0.97, "bullpen_support_factor": 0.96, "run_environment_factor": 1.03},
    "SF":  {"offense_strength": 0.99, "offense_k_tendency": 0.99, "win_support_factor": 0.99, "bullpen_support_factor": 1.01, "run_environment_factor": 0.98},
    "Col": {"offense_strength": 0.96, "offense_k_tendency": 1.05, "win_support_factor": 0.95, "bullpen_support_factor": 0.94, "run_environment_factor": 1.10},
}

# Neutral context returned when a team code is not found in the map
NEUTRAL_TEAM_CTX: dict[str, float] = {
    "offense_strength": 1.0,
    "offense_k_tendency": 1.0,
    "win_support_factor": 1.0,
    "bullpen_support_factor": 1.0,
    "run_environment_factor": 1.0,
}


def get_default_team_context(season_year: int) -> list[dict]:
    """Return static default team context records for all 30 MLB teams.

    Used as fallback when real API data is unavailable.
    """
    rows = [
        {"team_code": code, "season_year": season_year, **factors}
        for code, factors in _DEFAULT_TEAMS.items()
    ]
    logger.info("Generated %d default team context records for %d", len(rows), season_year)
    return rows


# ── Real team-metrics ingestion from MLB Stats API ──

def _fetch_mlb_team_map(season_year: int) -> dict[int, str]:
    """Fetch MLB teams list and return {team_id: normalized_team_code}."""
    logger.info("Fetching MLB team list for %d", season_year)
    resp = resilient_get(
        f"{MLB_API_BASE}/teams",
        params={"sportId": 1, "season": season_year},
        label="team_list",
    )

    teams: dict[int, str] = {}
    for t in resp.json().get("teams", []):
        team_id = t.get("id")
        abbrev = t.get("abbreviation", "")
        if team_id and abbrev:
            teams[team_id] = _normalize_abbrev(abbrev)

    logger.info("Fetched %d MLB teams for %d", len(teams), season_year)
    return teams


def _fetch_team_group_stats(season_year: int, group: str) -> dict[int, dict]:
    """Fetch team-level aggregate stats for a stat group ('hitting' or 'pitching').

    Returns {team_id: stat_dict} from the MLB Stats API.
    """
    logger.info("Fetching team %s stats for %d", group, season_year)
    resp = resilient_get(
        f"{MLB_API_BASE}/teams/stats",
        params={
            "stats": "season",
            "group": group,
            "season": season_year,
            "sportId": 1,
            "gameType": "R",
        },
        label=f"team_{group}_stats",
    )

    stats_list = resp.json().get("stats", [])
    if not stats_list:
        raise ValueError(f"No team {group} stats returned for {season_year}")

    result: dict[int, dict] = {}
    for split in stats_list[0].get("splits", []):
        team_id = split.get("team", {}).get("id")
        stat = split.get("stat", {})
        if team_id and stat:
            result[team_id] = stat

    logger.info("Fetched %s stats for %d teams", group, len(result))
    return result


def normalize_team_metrics(
    teams_map: dict[int, str],
    hitting_stats: dict[int, dict],
    pitching_stats: dict[int, dict],
    season_year: int,
) -> list[dict]:
    """Normalize raw MLB team stats into bounded team_context factor rows.

    Each factor is computed relative to the league average and clamped to
    [0.85, 1.15] to prevent extreme projection swings.

    Derivations:
      offense_strength      = team runs/game / league avg runs/game
      offense_k_tendency    = team batting K rate / league avg K rate
      win_support_factor    = 60% run production + 40% win percentage (relative)
      bullpen_support_factor = 40% inverse ERA + 60% save conversion rate (relative)
      run_environment_factor = team total runs (scored+allowed)/game / league avg
    """
    # Collect per-team raw metrics
    team_data: list[dict] = []
    for team_id, team_code in teams_map.items():
        h = hitting_stats.get(team_id)
        p = pitching_stats.get(team_id)
        if not h or not p:
            continue

        games = max(1, _safe_float(h.get("gamesPlayed", 0)))
        if games < 1:
            continue

        # -- Hitting metrics --
        runs_scored = _safe_float(h.get("runs", 0))
        k_batting = _safe_float(h.get("strikeOuts", 0))
        pa = _safe_float(h.get("plateAppearances")) or _safe_float(h.get("atBats")) or 1.0

        rpg = runs_scored / games
        k_rate = k_batting / pa if pa > 0 else 0.22

        # -- Pitching metrics --
        era = _safe_float(p.get("era", "4.50"))
        if era <= 0:
            era = 4.50
        wins = _safe_float(p.get("wins", 0))
        losses = _safe_float(p.get("losses", 0))
        saves = _safe_float(p.get("saves", 0))
        blown_saves = _safe_float(p.get("blownSaves", 0))
        runs_allowed = _safe_float(p.get("runs", 0))

        total_decisions = wins + losses
        win_pct = wins / total_decisions if total_decisions > 0 else 0.500
        save_total = saves + blown_saves
        save_pct = saves / save_total if save_total > 0 else 0.650
        ra_per_game = runs_allowed / games
        total_rpg = rpg + ra_per_game

        team_data.append({
            "team_code": team_code,
            "rpg": rpg,
            "k_rate": k_rate,
            "era": era,
            "win_pct": win_pct,
            "save_pct": save_pct,
            "total_rpg": total_rpg,
        })

    if not team_data:
        raise ValueError("No valid team data to normalize")

    # -- Compute league averages --
    n = len(team_data)
    avg_rpg = sum(t["rpg"] for t in team_data) / n
    avg_k_rate = sum(t["k_rate"] for t in team_data) / n
    avg_era = sum(t["era"] for t in team_data) / n
    avg_save_pct = sum(t["save_pct"] for t in team_data) / n
    avg_total_rpg = sum(t["total_rpg"] for t in team_data) / n

    logger.info(
        "League averages (%d): rpg=%.2f, k_rate=%.3f, era=%.2f, "
        "save_pct=%.3f, total_rpg=%.2f",
        season_year, avg_rpg, avg_k_rate, avg_era, avg_save_pct, avg_total_rpg,
    )

    # -- Normalize each team --
    rows: list[dict] = []
    for t in team_data:
        # offense_strength: runs per game relative to league
        os_val = _clamp(t["rpg"] / avg_rpg) if avg_rpg > 0 else 1.0

        # offense_k_tendency: K rate relative to league (higher = more K-prone)
        kt_val = _clamp(t["k_rate"] / avg_k_rate) if avg_k_rate > 0 else 1.0

        # win_support_factor: blend of run production (60%) and win% (40%)
        rpg_rel = t["rpg"] / avg_rpg if avg_rpg > 0 else 1.0
        win_rel = t["win_pct"] / 0.500
        ws_val = _clamp(rpg_rel * 0.6 + win_rel * 0.4)

        # bullpen_support_factor: blend of inverse ERA quality (40%) and
        # save conversion rate (60%) — save rate is more bullpen-specific
        era_inv = avg_era / t["era"] if t["era"] > 0 else 1.0
        save_rel = t["save_pct"] / avg_save_pct if avg_save_pct > 0 else 1.0
        bp_val = _clamp(era_inv * 0.4 + save_rel * 0.6)

        # run_environment_factor: total runs in team games vs league average
        re_val = _clamp(t["total_rpg"] / avg_total_rpg) if avg_total_rpg > 0 else 1.0

        rows.append({
            "team_code": t["team_code"],
            "season_year": season_year,
            "offense_strength": round(os_val, 3),
            "offense_k_tendency": round(kt_val, 3),
            "win_support_factor": round(ws_val, 3),
            "bullpen_support_factor": round(bp_val, 3),
            "run_environment_factor": round(re_val, 3),
        })

    logger.info("Normalized %d teams from real MLB metrics for %d", len(rows), season_year)
    return rows


def fetch_real_team_metrics(season_year: int) -> list[dict]:
    """Fetch and normalize real current-season team metrics from the MLB Stats API.

    Makes 3 API calls: team list, team hitting stats, team pitching stats.
    Returns a list of normalized team_context dicts ready for upsert.
    Raises on any API or parsing error.
    """
    logger.info("Starting real team metrics ingestion for %d", season_year)

    teams_map = _fetch_mlb_team_map(season_year)
    hitting = _fetch_team_group_stats(season_year, "hitting")
    pitching = _fetch_team_group_stats(season_year, "pitching")

    rows = normalize_team_metrics(teams_map, hitting, pitching, season_year)
    logger.info(
        "Real team metrics ingestion complete: %d teams for %d", len(rows), season_year,
    )
    return rows


# ── Database operations ──

def upsert_team_context(session: Session, context_rows: list[dict]) -> int:
    """Insert or update team context rows by (team_code, season_year).

    Flushes but does NOT commit — caller is responsible for committing.
    Returns the number of rows upserted.
    """
    now = datetime.now(timezone.utc)
    count = 0

    for row in context_rows:
        existing = (
            session.query(TeamContext)
            .filter(
                TeamContext.team_code == row["team_code"],
                TeamContext.season_year == row["season_year"],
            )
            .first()
        )

        if existing:
            existing.offense_strength = row.get("offense_strength", existing.offense_strength)
            existing.offense_k_tendency = row.get("offense_k_tendency", existing.offense_k_tendency)
            existing.win_support_factor = row.get("win_support_factor", existing.win_support_factor)
            existing.bullpen_support_factor = row.get("bullpen_support_factor", existing.bullpen_support_factor)
            existing.run_environment_factor = row.get("run_environment_factor", existing.run_environment_factor)
            existing.updated_at = now
        else:
            session.add(TeamContext(
                team_code=row["team_code"],
                season_year=row["season_year"],
                offense_strength=row.get("offense_strength"),
                offense_k_tendency=row.get("offense_k_tendency"),
                win_support_factor=row.get("win_support_factor"),
                bullpen_support_factor=row.get("bullpen_support_factor"),
                run_environment_factor=row.get("run_environment_factor"),
            ))
        count += 1

    session.flush()
    return count


def refresh_team_context(session: Session, season_year: int) -> dict:
    """Refresh team context: try real MLB metrics first, fall back to defaults.

    Upserts rows into team_context (flushes but does NOT commit).
    Returns a summary dict with source and fallback info.
    """
    try:
        rows = fetch_real_team_metrics(season_year)
        source = "mlb"
        fallback_used = False
        logger.info("Using real MLB team metrics for %d (%d teams)", season_year, len(rows))
    except Exception as e:
        logger.error("Real team metrics fetch failed: %s — falling back to defaults", e)
        rows = get_default_team_context(season_year)
        source = "defaults"
        fallback_used = True

    count = upsert_team_context(session, rows)

    return {
        "season_year": season_year,
        "teams_upserted": count,
        "source": source,
        "fallback_used": fallback_used,
    }


def get_team_context_map(session: Session, season_year: int) -> dict[str, dict]:
    """Load team context for a season, keyed by team_code.

    If no rows exist for the season, auto-refreshes (tries real MLB data
    first, then falls back to defaults) and persists them.
    Returns a dict like {"NYY": {"offense_strength": 1.07, ...}, ...}.
    """
    rows = (
        session.query(TeamContext)
        .filter(TeamContext.season_year == season_year)
        .all()
    )

    if not rows:
        logger.info("No team context for %d — auto-refreshing", season_year)
        refresh_team_context(session, season_year)
        session.commit()
        rows = (
            session.query(TeamContext)
            .filter(TeamContext.season_year == season_year)
            .all()
        )

    logger.info("Loaded team context: %d teams for %d", len(rows), season_year)

    return {
        r.team_code: {
            "offense_strength": r.offense_strength,
            "offense_k_tendency": r.offense_k_tendency,
            "win_support_factor": r.win_support_factor,
            "bullpen_support_factor": r.bullpen_support_factor,
            "run_environment_factor": r.run_environment_factor,
        }
        for r in rows
    }
