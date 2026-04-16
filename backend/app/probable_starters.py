"""
Probable starter ingestion — fetches real probable pitchers from the MLB Stats API,
with graceful fallback to demo data if the API is unavailable.

MLB Stats API endpoint (public, no auth required):
  https://statsapi.mlb.com/api/v1/schedule?date=YYYY-MM-DD&sportId=1&hydrate=probablePitcher

Designed for easy replacement: swap fetch_probable_starters_from_mlb() with a
different source without changing the rest of the pipeline.
"""
import logging
from datetime import date

from app.http_client import resilient_get
from app.projection_builder import StarterRecord, build_demo_probable_starters

logger = logging.getLogger(__name__)

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

# MLB API abbreviations are mostly standard, but normalize a few edge cases
_TEAM_ABBREV_OVERRIDES = {
    "AZ": "Ari",
    "ARI": "Ari",
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


def _normalize_team_abbrev(abbrev: str) -> str:
    """Normalize an MLB API team abbreviation to our internal format."""
    upper = abbrev.strip().upper()
    if upper in _TEAM_ABBREV_OVERRIDES:
        return _TEAM_ABBREV_OVERRIDES[upper]
    # Default: capitalize first letter only (e.g. "BOS" -> "Bos")
    return abbrev.strip().capitalize() if len(abbrev) <= 3 else abbrev.strip()


def _normalize_starter_record(
    game_date: date,
    game_pk: int,
    pitcher_data: dict,
    pitcher_team_abbrev: str,
    opponent_team_abbrev: str,
    home_away: str,
    status: str,
) -> StarterRecord | None:
    """Build a StarterRecord from MLB API pitcher data. Returns None if data is missing."""
    if not pitcher_data:
        return None

    name = pitcher_data.get("fullName")
    if not name:
        return None

    pitch_hand = pitcher_data.get("pitchHand", {})
    throws = pitch_hand.get("code") if pitch_hand else None
    player_id = pitcher_data.get("id")

    return StarterRecord(
        game_date=game_date,
        game_id=str(game_pk),
        pitcher_name=name,
        pitcher_team=_normalize_team_abbrev(pitcher_team_abbrev),
        opponent_team=_normalize_team_abbrev(opponent_team_abbrev),
        home_away=home_away,
        throws=throws or "",
        status=status,
        source="mlb",
        mlb_player_id=player_id,
    )


def fetch_probable_starters_from_mlb(target_date: date) -> list[StarterRecord]:
    """Fetch probable starters for a date from the MLB Stats API.

    Returns a list of StarterRecords. Raises on network/parse errors.
    """
    date_str = target_date.isoformat()
    logger.info("Fetching probable starters from MLB API for %s", date_str)

    resp = resilient_get(
        MLB_SCHEDULE_URL,
        params={
            "date": date_str,
            "sportId": 1,
            "hydrate": "probablePitcher",
        },
        label="probable_starters",
    )

    data = resp.json()
    dates = data.get("dates", [])
    if not dates:
        logger.warning("MLB API returned no dates for %s", date_str)
        return []

    starters: list[StarterRecord] = []

    for date_entry in dates:
        games = date_entry.get("games", [])
        for game in games:
            game_pk = game.get("gamePk", 0)
            game_status = game.get("status", {}).get("abstractGameState", "")

            # Skip completed or postponed games
            if game_status in ("Final", "Postponed", "Cancelled"):
                continue

            teams = game.get("teams", {})
            away = teams.get("away", {})
            home = teams.get("home", {})

            away_team = away.get("team", {})
            home_team = home.get("team", {})

            away_abbrev = away_team.get("abbreviation", "")
            home_abbrev = home_team.get("abbreviation", "")

            status = "confirmed" if game_status == "Preview" else "probable"

            # Away pitcher
            away_pitcher = away.get("probablePitcher")
            if away_pitcher:
                rec = _normalize_starter_record(
                    target_date, game_pk, away_pitcher,
                    away_abbrev, home_abbrev, "away", status,
                )
                if rec:
                    starters.append(rec)

            # Home pitcher
            home_pitcher = home.get("probablePitcher")
            if home_pitcher:
                rec = _normalize_starter_record(
                    target_date, game_pk, home_pitcher,
                    home_abbrev, away_abbrev, "home", status,
                )
                if rec:
                    starters.append(rec)

    logger.info("MLB API returned %d probable starters for %s", len(starters), date_str)
    return starters


def get_probable_starters(target_date: date) -> tuple[list[StarterRecord], bool]:
    """Get probable starters for a date. Tries MLB API first, falls back to demo.

    Returns:
        (starters, used_fallback) — the list and whether demo fallback was used.
    """
    try:
        starters = fetch_probable_starters_from_mlb(target_date)
        if len(starters) >= 2:
            logger.info("Using %d real MLB starters for %s", len(starters), target_date)
            return starters, False
        else:
            logger.warning(
                "MLB API returned only %d starters for %s — falling back to demo",
                len(starters), target_date,
            )
    except Exception as e:
        logger.error("MLB probable starter fetch failed: %s — falling back to demo", e)

    # Fallback to demo
    starters = build_demo_probable_starters(target_date)
    for s in starters:
        s.source = "demo-fallback"
    logger.info("Using %d demo fallback starters for %s", len(starters), target_date)
    return starters, True
