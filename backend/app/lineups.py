"""
Projected lineup ingestion — fetches expected lineups from the MLB Stats API
and builds per-team batting aggregates for lineup-aware pitcher projections.

MLB Stats API endpoints (public, no auth):
  Game-level lineups:
    https://statsapi.mlb.com/api/v1.1/game/{gamePk}/feed/live  (has lineupOrder)
    https://statsapi.mlb.com/api/v1/schedule?date=...&sportId=1&hydrate=lineups
  Player season hitting stats:
    https://statsapi.mlb.com/api/v1/people/{id}?hydrate=stats(type=season,season=YYYY,group=hitting)

Fallback: when the API is unavailable or lineups aren't posted yet, generates
neutral default aggregates (all factors = 1.0) so the projection engine
degrades gracefully to team-context-only adjustments.

Model version: formula-v4-lineups
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

import requests
from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import Session

from app.models import LineupAggregate, ProjectedLineup

logger = logging.getLogger(__name__)

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"
REQUEST_TIMEOUT = 15

# ── Team abbreviation normalization (shared pattern with other modules) ──

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
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float = 0.85, hi: float = 1.15) -> float:
    return max(lo, min(hi, value))


# ── Data classes ──

@dataclass
class LineupSlot:
    """One hitter in a projected lineup."""
    game_date: date
    game_id: str
    team_code: str
    batting_order: int
    hitter_name: str
    bats: str | None = None
    mlb_player_id: int | None = None
    confirmed: bool = False
    source: str = "mlb"


@dataclass
class LineupAgg:
    """Aggregated batting tendencies for a team's lineup vs a pitching hand."""
    game_date: date
    game_id: str
    team_code: str
    vs_hand: str  # "L", "R", or "overall"
    agg_k_tendency: float    # relative to league avg (1.0 = neutral)
    agg_bb_tendency: float
    agg_offense_strength: float
    agg_contact_quality: float
    hitter_count: int
    source: str = "mlb"


# ── Neutral defaults for when lineups are unavailable ──

NEUTRAL_LINEUP_AGG: dict[str, float] = {
    "agg_k_tendency": 1.0,
    "agg_bb_tendency": 1.0,
    "agg_offense_strength": 1.0,
    "agg_contact_quality": 1.0,
    "hitter_count": 0,
}


# ── MLB API: fetch lineups from schedule ──

def _fetch_game_pks_for_date(target_date: date) -> list[dict]:
    """Fetch game schedule for a date. Returns list of {gamePk, away_team, home_team}."""
    date_str = target_date.isoformat()
    logger.info("Fetching game schedule for %s", date_str)

    resp = requests.get(
        f"{MLB_API_BASE}/schedule",
        params={"date": date_str, "sportId": 1},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()

    games_info = []
    for date_entry in resp.json().get("dates", []):
        for game in date_entry.get("games", []):
            game_pk = game.get("gamePk")
            status = game.get("status", {}).get("abstractGameState", "")
            if status in ("Final", "Postponed", "Cancelled"):
                continue

            teams = game.get("teams", {})
            away_abbrev = teams.get("away", {}).get("team", {}).get("abbreviation", "")
            home_abbrev = teams.get("home", {}).get("team", {}).get("abbreviation", "")

            if game_pk and away_abbrev and home_abbrev:
                games_info.append({
                    "gamePk": game_pk,
                    "away_team": _normalize_abbrev(away_abbrev),
                    "home_team": _normalize_abbrev(home_abbrev),
                })

    logger.info("Found %d games for %s", len(games_info), date_str)
    return games_info


def _fetch_lineup_from_game_feed(game_pk: int) -> dict[str, list[dict]]:
    """Fetch lineup data from game feed. Returns {team_type: [{player info}]}.

    Uses the game feed endpoint which includes lineup info when available.
    Returns {"away": [...], "home": [...]} where each list has player dicts.
    """
    url = f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.debug("Game feed unavailable for gamePk %d: %s", game_pk, e)
        return {"away": [], "home": []}

    result: dict[str, list[dict]] = {"away": [], "home": []}

    game_data = data.get("gameData", {})
    players = game_data.get("players", {})

    live_data = data.get("liveData", {})
    boxscore = live_data.get("boxscore", {})

    for side in ("away", "home"):
        team_box = boxscore.get("teams", {}).get(side, {})
        batting_order = team_box.get("battingOrder", [])

        if not batting_order:
            continue

        for order_idx, player_id in enumerate(batting_order, start=1):
            player_key = f"ID{player_id}"
            player_info = players.get(player_key, {})
            if not player_info:
                continue

            name = player_info.get("fullName", "")
            bat_side = player_info.get("batSide", {}).get("code", "")

            if name:
                result[side].append({
                    "batting_order": order_idx,
                    "hitter_name": name,
                    "bats": bat_side or None,
                    "mlb_player_id": player_id,
                })

    return result


def fetch_projected_lineups_for_date(
    target_date: date,
) -> list[LineupSlot]:
    """Fetch projected lineups for all games on a date from the MLB API.

    Tries the game feed for each game to find batting orders. Returns
    a flat list of LineupSlot records.
    """
    logger.info("Fetching projected lineups for %s", target_date)

    games = _fetch_game_pks_for_date(target_date)
    if not games:
        logger.warning("No games found for %s", target_date)
        return []

    all_slots: list[LineupSlot] = []
    games_with_lineups = 0

    for game_info in games:
        game_pk = game_info["gamePk"]
        game_id = str(game_pk)

        lineup_data = _fetch_lineup_from_game_feed(game_pk)

        for side, team_code in [("away", game_info["away_team"]),
                                ("home", game_info["home_team"])]:
            players = lineup_data.get(side, [])
            if not players:
                continue

            confirmed = len(players) >= 9
            if confirmed:
                games_with_lineups += 1

            for p in players:
                all_slots.append(LineupSlot(
                    game_date=target_date,
                    game_id=game_id,
                    team_code=team_code,
                    batting_order=p["batting_order"],
                    hitter_name=p["hitter_name"],
                    bats=p.get("bats"),
                    mlb_player_id=p.get("mlb_player_id"),
                    confirmed=confirmed,
                    source="mlb",
                ))

    logger.info(
        "Fetched %d lineup slots across %d games (%d with full lineups) for %s",
        len(all_slots), len(games), games_with_lineups, target_date,
    )
    return all_slots


# ── Hitter season stats for aggregation ──

def _fetch_hitter_season_stats(player_id: int, season_year: int) -> dict | None:
    """Fetch season hitting stats for a single hitter. Returns stat dict or None."""
    url = f"{MLB_API_BASE}/people/{player_id}"
    params = {"hydrate": f"stats(type=season,season={season_year},group=hitting)"}

    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.debug("Hitter stats fetch failed for player %d: %s", player_id, e)
        return None

    people = data.get("people", [])
    if not people:
        return None

    for stats_group in people[0].get("stats", []):
        splits = stats_group.get("splits", [])
        if splits:
            return splits[0].get("stat")
    return None


# ── Aggregate computation ──

def build_lineup_aggregates(
    slots: list[LineupSlot],
    season_year: int,
) -> list[LineupAgg]:
    """Build batting aggregates from lineup slots by fetching each hitter's season stats.

    For each (game_date, game_id, team_code) group, computes aggregate K tendency,
    BB tendency, offense strength, and contact quality relative to league averages.
    Returns one LineupAgg per team per game with vs_hand="overall".
    """
    # Group slots by (game_date, game_id, team_code)
    groups: dict[tuple, list[LineupSlot]] = {}
    for slot in slots:
        key = (slot.game_date, slot.game_id, slot.team_code)
        groups.setdefault(key, []).append(slot)

    # League-average benchmarks (2024-2025 MLB hitter averages)
    lg_k_rate = 0.225     # ~22.5% K rate
    lg_bb_rate = 0.085    # ~8.5% BB rate
    lg_ops = 0.710        # ~.710 OPS
    lg_babip = 0.295      # ~.295 BABIP

    aggregates: list[LineupAgg] = []

    for (game_date, game_id, team_code), team_slots in groups.items():
        # Fetch season stats for each hitter with an MLB player ID
        k_rates = []
        bb_rates = []
        ops_values = []
        babip_values = []
        hitter_count = 0

        for slot in sorted(team_slots, key=lambda s: s.batting_order):
            if not slot.mlb_player_id:
                continue

            stats = _fetch_hitter_season_stats(slot.mlb_player_id, season_year)
            if not stats:
                continue

            pa = _safe_float(stats.get("plateAppearances")) or _safe_float(stats.get("atBats"))
            if pa < 20:
                continue

            hitter_count += 1

            so = _safe_float(stats.get("strikeOuts", 0))
            bb = _safe_float(stats.get("baseOnBalls", 0))

            k_rate = so / pa if pa > 0 else lg_k_rate
            bb_rate = bb / pa if pa > 0 else lg_bb_rate

            try:
                ops = _safe_float(stats.get("ops", "0"))
            except (TypeError, ValueError):
                ops = lg_ops

            try:
                babip = _safe_float(stats.get("babip", "0"))
            except (TypeError, ValueError):
                babip = lg_babip

            k_rates.append(k_rate)
            bb_rates.append(bb_rate)
            ops_values.append(ops if ops > 0 else lg_ops)
            babip_values.append(babip if babip > 0 else lg_babip)

        if hitter_count < 3:
            # Not enough data — produce neutral aggregate
            aggregates.append(LineupAgg(
                game_date=game_date,
                game_id=game_id,
                team_code=team_code,
                vs_hand="overall",
                agg_k_tendency=1.0,
                agg_bb_tendency=1.0,
                agg_offense_strength=1.0,
                agg_contact_quality=1.0,
                hitter_count=hitter_count,
                source="mlb-insufficient",
            ))
            continue

        # Compute averages
        avg_k = sum(k_rates) / len(k_rates)
        avg_bb = sum(bb_rates) / len(bb_rates)
        avg_ops = sum(ops_values) / len(ops_values)
        avg_babip = sum(babip_values) / len(babip_values)

        # Normalize relative to league averages, clamp to [0.85, 1.15]
        agg_k_tendency = _clamp(avg_k / lg_k_rate) if lg_k_rate > 0 else 1.0
        agg_bb_tendency = _clamp(avg_bb / lg_bb_rate) if lg_bb_rate > 0 else 1.0
        agg_offense_strength = _clamp(avg_ops / lg_ops) if lg_ops > 0 else 1.0
        agg_contact_quality = _clamp(avg_babip / lg_babip) if lg_babip > 0 else 1.0

        aggregates.append(LineupAgg(
            game_date=game_date,
            game_id=game_id,
            team_code=team_code,
            vs_hand="overall",
            agg_k_tendency=round(agg_k_tendency, 3),
            agg_bb_tendency=round(agg_bb_tendency, 3),
            agg_offense_strength=round(agg_offense_strength, 3),
            agg_contact_quality=round(agg_contact_quality, 3),
            hitter_count=hitter_count,
            source="mlb",
        ))

    logger.info(
        "Built %d lineup aggregates from %d lineup slots for %d team-games",
        len(aggregates), len(slots), len(groups),
    )
    return aggregates


def get_default_lineup_aggregates(
    target_date: date,
    game_ids_and_teams: list[tuple[str, str]],
) -> list[LineupAgg]:
    """Generate neutral lineup aggregates (all factors = 1.0) as fallback.

    Args:
        target_date: the game date
        game_ids_and_teams: list of (game_id, team_code) tuples
    """
    return [
        LineupAgg(
            game_date=target_date,
            game_id=game_id,
            team_code=team_code,
            vs_hand="overall",
            agg_k_tendency=1.0,
            agg_bb_tendency=1.0,
            agg_offense_strength=1.0,
            agg_contact_quality=1.0,
            hitter_count=0,
            source="default",
        )
        for game_id, team_code in game_ids_and_teams
    ]


# ── Database operations ──

def write_lineups_to_db(
    session: Session,
    slots: list[LineupSlot],
    target_date: date,
) -> int:
    """Write projected lineup slots to DB, replacing any existing for the date.

    Flushes but does NOT commit — caller is responsible for committing.
    """
    session.execute(
        sa_delete(ProjectedLineup).where(ProjectedLineup.game_date == target_date)
    )

    for slot in slots:
        session.add(ProjectedLineup(
            game_date=slot.game_date,
            game_id=slot.game_id,
            team_code=slot.team_code,
            batting_order=slot.batting_order,
            hitter_name=slot.hitter_name,
            bats=slot.bats,
            confirmed=slot.confirmed,
            source=slot.source,
        ))

    session.flush()
    logger.info("Wrote %d lineup slots for %s", len(slots), target_date)
    return len(slots)


def write_aggregates_to_db(
    session: Session,
    aggregates: list[LineupAgg],
    target_date: date,
) -> int:
    """Write lineup aggregates to DB, replacing any existing for the date.

    Flushes but does NOT commit — caller is responsible for committing.
    """
    session.execute(
        sa_delete(LineupAggregate).where(LineupAggregate.game_date == target_date)
    )

    for agg in aggregates:
        session.add(LineupAggregate(
            game_date=agg.game_date,
            game_id=agg.game_id,
            team_code=agg.team_code,
            vs_hand=agg.vs_hand,
            agg_k_tendency=agg.agg_k_tendency,
            agg_bb_tendency=agg.agg_bb_tendency,
            agg_offense_strength=agg.agg_offense_strength,
            agg_contact_quality=agg.agg_contact_quality,
            hitter_count=agg.hitter_count,
            source=agg.source,
        ))

    session.flush()
    logger.info("Wrote %d lineup aggregates for %s", len(aggregates), target_date)
    return len(aggregates)


def get_lineup_aggregate_map(
    session: Session,
    target_date: date,
) -> dict[str, dict]:
    """Load lineup aggregates for a date, keyed by team_code.

    Returns a dict like {"NYY": {"agg_k_tendency": 1.05, ...}, ...}.
    Only returns "overall" aggregates (vs_hand="overall").
    """
    rows = (
        session.query(LineupAggregate)
        .filter(
            LineupAggregate.game_date == target_date,
            LineupAggregate.vs_hand == "overall",
        )
        .all()
    )

    logger.info("Loaded %d lineup aggregates for %s", len(rows), target_date)

    return {
        r.team_code: {
            "agg_k_tendency": r.agg_k_tendency,
            "agg_bb_tendency": r.agg_bb_tendency,
            "agg_offense_strength": r.agg_offense_strength,
            "agg_contact_quality": r.agg_contact_quality,
            "hitter_count": r.hitter_count,
        }
        for r in rows
    }


# ── Top-level refresh ──

def refresh_projected_lineups_and_aggregates(
    session: Session,
    target_date: date,
    season_year: int,
) -> dict:
    """Refresh projected lineups and aggregates for a date.

    1. Fetches lineups from MLB API
    2. Writes lineup slots to DB
    3. Builds and writes aggregates from hitter season stats
    4. Falls back to neutral aggregates on failure

    Commits the transaction. Returns a summary dict.
    """
    fallback_used = False

    try:
        slots = fetch_projected_lineups_for_date(target_date)
        source = "mlb"
    except Exception as e:
        logger.error("Lineup fetch failed for %s: %s — using empty lineups", target_date, e)
        slots = []
        source = "default"
        fallback_used = True

    # Write lineup slots
    slot_count = write_lineups_to_db(session, slots, target_date)

    # Build aggregates
    if slots:
        try:
            aggregates = build_lineup_aggregates(slots, season_year)
            if not aggregates:
                raise ValueError("No aggregates produced from lineup slots")
        except Exception as e:
            logger.error("Aggregate build failed: %s — using neutral defaults", e)
            # Build neutral defaults for each team that had lineup slots
            seen = set()
            game_teams = []
            for s in slots:
                key = (s.game_id, s.team_code)
                if key not in seen:
                    seen.add(key)
                    game_teams.append(key)
            aggregates = get_default_lineup_aggregates(target_date, game_teams)
            fallback_used = True
    else:
        aggregates = []

    agg_count = write_aggregates_to_db(session, aggregates, target_date)
    session.commit()

    summary = {
        "date": target_date.isoformat(),
        "lineup_slots_written": slot_count,
        "aggregates_written": agg_count,
        "source": source,
        "fallback_used": fallback_used,
    }
    logger.info("Lineup refresh complete for %s: %s", target_date, summary)
    return summary
