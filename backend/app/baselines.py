"""
Pitcher baseline ingestion — fetches real season stats from the MLB Stats API
and computes lightweight baseline metrics for projection input.

Falls back to demo baselines per-pitcher if the API is unavailable or the
pitcher has insufficient data.

MLB Stats API (public, no auth):
  https://statsapi.mlb.com/api/v1/people/{id}?hydrate=stats(type=season,season=YYYY,group=pitching)
  https://statsapi.mlb.com/api/v1/people/search?names=...

Designed for easy replacement: swap the fetch functions with FanGraphs/Statcast
sources later without changing the rest of the pipeline.
"""
import logging
from datetime import date

import requests

from app.projection_builder import BaselineRecord, StarterRecord, build_demo_baselines

logger = logging.getLogger(__name__)

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"
REQUEST_TIMEOUT = 12  # seconds


# ── MLB IP parsing ──

def _parse_mlb_ip(ip_str: str) -> float:
    """Parse MLB API innings pitched string. '180.2' means 180 + 2/3 innings."""
    if not ip_str:
        return 0.0
    try:
        parts = str(ip_str).split(".")
        whole = int(parts[0])
        thirds = int(parts[1]) if len(parts) > 1 else 0
        return whole + thirds / 3.0
    except (ValueError, IndexError):
        return 0.0


# ── MLB Stats API calls ──

def _fetch_pitcher_stats_by_id(player_id: int, season_year: int) -> dict | None:
    """Fetch season pitching stats from MLB API using player ID.
    Returns the raw 'stat' dict or None."""
    url = f"{MLB_API_BASE}/people/{player_id}"
    params = {"hydrate": f"stats(type=season,season={season_year},group=pitching)"}

    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("MLB stats fetch failed for player %d: %s", player_id, e)
        return None

    people = data.get("people", [])
    if not people:
        return None

    person = people[0]
    stats_list = person.get("stats", [])
    for stats_group in stats_list:
        splits = stats_group.get("splits", [])
        if splits:
            return splits[0].get("stat")

    return None


def _search_player_id(pitcher_name: str) -> int | None:
    """Search MLB API for a player by name. Returns their ID or None."""
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/people/search",
            params={"names": pitcher_name, "sportId": 1},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("MLB player search failed for '%s': %s", pitcher_name, e)
        return None

    rows = data.get("people", [])
    if not rows:
        # Try the searchPlayers endpoint as backup
        try:
            resp = requests.get(
                f"{MLB_API_BASE}/people/search",
                params={"names": pitcher_name},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            rows = resp.json().get("people", [])
        except Exception:
            pass

    if not rows:
        logger.warning("No MLB player found for '%s'", pitcher_name)
        return None

    # Pick the best match — prefer active pitchers
    for p in rows:
        if p.get("active", False) and p.get("primaryPosition", {}).get("abbreviation") == "P":
            return p["id"]
    # Fallback to first result
    return rows[0].get("id")


# ── Baseline computation ──

def _compute_baseline(
    stats: dict,
    pitcher_name: str,
    pitcher_team: str,
    season_year: int,
) -> BaselineRecord | None:
    """Compute baseline fields from an MLB API season pitching stat dict."""

    ip = _parse_mlb_ip(stats.get("inningsPitched", "0"))
    gs = stats.get("gamesStarted", 0) or 0

    if ip < 5.0 or gs < 1:
        logger.info("Insufficient data for %s: IP=%.1f, GS=%d", pitcher_name, ip, gs)
        return None

    ip_per_start = round(ip / gs, 1)

    # Rate stats from batters faced
    bf = stats.get("battersFaced", 0) or 0
    so = stats.get("strikeOuts", 0) or 0
    bb = stats.get("baseOnBalls", 0) or 0

    # If BF not available, approximate: BF ≈ IP * 4.3 (league average)
    if bf < 1:
        bf = int(ip * 4.3)

    k_pct = round(so / bf * 100, 1) if bf > 0 else 0.0
    bb_pct = round(bb / bf * 100, 1) if bf > 0 else 0.0

    # ERA and WHIP — MLB API provides these as strings
    try:
        era = round(float(stats.get("era", "0")), 2)
    except (ValueError, TypeError):
        era = round(stats.get("earnedRuns", 0) * 9.0 / ip, 2) if ip > 0 else 0.0

    try:
        whip = round(float(stats.get("whip", "0")), 2)
    except (ValueError, TypeError):
        h = stats.get("hits", 0) or 0
        whip = round((h + bb) / ip, 2) if ip > 0 else 0.0

    return BaselineRecord(
        pitcher_name=pitcher_name,
        pitcher_team=pitcher_team,
        season_year=season_year,
        ros_ip_per_start=ip_per_start,
        ros_k_pct=k_pct,
        ros_bb_pct=bb_pct,
        ros_era=era,
        ros_whip=whip,
        xera=None,   # requires Statcast — future enhancement
        xwoba=None,  # requires Statcast — future enhancement
    )


# ── Public API ──

def get_real_pitcher_baseline(
    pitcher_name: str,
    pitcher_team: str,
    season_year: int,
    mlb_player_id: int | None = None,
) -> BaselineRecord | None:
    """Attempt to fetch a real baseline for one pitcher from the MLB Stats API.

    Uses mlb_player_id directly if available, otherwise searches by name.
    Returns None if the API fails or the pitcher has insufficient data.
    """
    player_id = mlb_player_id

    # If we don't have a player ID, search by name
    if not player_id:
        player_id = _search_player_id(pitcher_name)
        if not player_id:
            return None

    # Fetch season stats
    stats = _fetch_pitcher_stats_by_id(player_id, season_year)
    if not stats:
        logger.info("No season stats for %s (id=%d, year=%d)", pitcher_name, player_id, season_year)
        return None

    baseline = _compute_baseline(stats, pitcher_name, pitcher_team, season_year)
    if baseline:
        logger.info("Real baseline for %s: ERA=%.2f, WHIP=%.2f, K%%=%.1f, IP/GS=%.1f",
                     pitcher_name, baseline.ros_era, baseline.ros_whip,
                     baseline.ros_k_pct, baseline.ros_ip_per_start)
    return baseline


def get_pitcher_baselines_for_starters(
    starters: list[StarterRecord],
    season_year: int,
) -> tuple[list[BaselineRecord], int]:
    """Get baselines for all starters. Tries real MLB stats first, falls back to
    demo baselines per-pitcher.

    Returns:
        (baselines, fallback_count)
    """
    logger.info("Building baselines for %d starters (season=%d)", len(starters), season_year)

    # Build demo baselines as fallback lookup
    demo_baselines = build_demo_baselines(starters, season_year)
    demo_map = {bl.pitcher_name: bl for bl in demo_baselines}

    baselines: list[BaselineRecord] = []
    fallback_count = 0
    real_count = 0

    for s in starters:
        mlb_id = getattr(s, "mlb_player_id", None)

        # Try real baseline
        try:
            bl = get_real_pitcher_baseline(s.pitcher_name, s.pitcher_team, season_year, mlb_id)
        except Exception as e:
            logger.warning("Baseline fetch error for %s: %s", s.pitcher_name, e)
            bl = None

        if bl:
            baselines.append(bl)
            real_count += 1
        else:
            # Fall back to demo for this pitcher only
            demo_bl = demo_map.get(s.pitcher_name)
            if demo_bl:
                logger.info("Using demo fallback baseline for %s", s.pitcher_name)
                baselines.append(demo_bl)
                fallback_count += 1
            else:
                logger.warning("No baseline available for %s (no real data, no demo profile)",
                               s.pitcher_name)

    logger.info("Baselines complete: %d real, %d fallback, %d total",
                real_count, fallback_count, len(baselines))
    return baselines, fallback_count


def upsert_pitcher_baselines(session, baselines: list[BaselineRecord], season_year: int) -> int:
    """Delete existing rows for these pitchers/season, then insert fresh ones.
    Returns count inserted."""
    from sqlalchemy import delete as sa_delete
    from app.models import PitcherBaseline

    pitcher_names = [bl.pitcher_name for bl in baselines]
    if not pitcher_names:
        return 0

    # Delete existing rows for these pitchers in this season
    session.execute(
        sa_delete(PitcherBaseline).where(
            PitcherBaseline.pitcher_name.in_(pitcher_names),
            PitcherBaseline.season_year == season_year,
        )
    )

    # Insert fresh rows
    for bl in baselines:
        session.add(PitcherBaseline(
            pitcher_name=bl.pitcher_name,
            pitcher_team=bl.pitcher_team,
            season_year=bl.season_year,
            ros_ip_per_start=bl.ros_ip_per_start,
            ros_k_pct=bl.ros_k_pct,
            ros_bb_pct=bl.ros_bb_pct,
            ros_era=bl.ros_era,
            ros_whip=bl.ros_whip,
            xera=bl.xera,
            xwoba=bl.xwoba,
        ))

    session.flush()
    logger.info("Upserted %d baselines for season %d", len(baselines), season_year)
    return len(baselines)
