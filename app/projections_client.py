"""Fetch player projections from FanGraphs (Steamer, ZiPS, ATC, THE BAT X)."""

import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

FANGRAPHS_API = "https://www.fangraphs.com/api/projections"

PROJECTION_SYSTEMS = {
    "steamer": {"type": "steamerr", "label": "Steamer"},
    "zips": {"type": "rzips", "label": "ZiPS"},
    "atc": {"type": "atc", "label": "ATC"},
    "thebatx": {"type": "thebatx", "label": "THE BAT X"},
}

# Key hitting stats we care about
HITTING_STATS = ["PA", "AB", "H", "HR", "R", "RBI", "SB", "BB", "SO", "AVG", "OBP", "SLG", "OPS"]

# Key pitching stats we care about
PITCHING_STATS = ["W", "L", "SV", "IP", "SO", "ERA", "WHIP", "K/9", "BB/9", "HR/9", "GS", "HLD"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.fangraphs.com/projections",
}


def _fetch_fangraphs_projections(system_type: str, stats: str = "bat", pos: str = "all") -> list[dict]:
    """Fetch projections from FanGraphs internal API."""
    params = {
        "type": system_type,
        "stats": stats,
        "pos": pos,
        "team": "0",
        "lg": "all",
        "players": "0",
    }
    try:
        resp = requests.get(FANGRAPHS_API, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        logger.warning("FanGraphs API request failed for %s/%s: %s", system_type, stats, e)
        return []


def _normalize_player_name(name: str) -> str:
    """Normalize for matching: lowercase, strip accents, remove suffixes."""
    import unicodedata
    name = unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode("ascii")
    name = name.lower().strip()
    for suffix in [" jr.", " sr.", " jr", " sr", " iii", " ii", " iv"]:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return name.strip()


def _parse_hitting_projections(raw: list[dict]) -> dict[str, dict]:
    """Parse raw FanGraphs hitting data into {normalized_name: {stat: value}}."""
    result = {}
    for player in raw:
        name = player.get("PlayerName") or player.get("playerName") or ""
        if not name:
            continue
        key = _normalize_player_name(name)
        stats = {}
        for stat in HITTING_STATS:
            val = player.get(stat)
            if val is not None:
                stats[stat] = round(float(val), 3) if isinstance(val, float) else val
        stats["_raw_name"] = name
        stats["_fg_id"] = player.get("playerid") or player.get("PlayerId")
        stats["_team"] = player.get("Team") or player.get("team") or ""
        result[key] = stats
    return result


def _parse_pitching_projections(raw: list[dict]) -> dict[str, dict]:
    """Parse raw FanGraphs pitching data into {normalized_name: {stat: value}}."""
    result = {}
    for player in raw:
        name = player.get("PlayerName") or player.get("playerName") or ""
        if not name:
            continue
        key = _normalize_player_name(name)
        stats = {}
        for stat in PITCHING_STATS:
            val = player.get(stat)
            if val is not None:
                stats[stat] = round(float(val), 3) if isinstance(val, float) else val
        stats["_raw_name"] = name
        stats["_fg_id"] = player.get("playerid") or player.get("PlayerId")
        stats["_team"] = player.get("Team") or player.get("team") or ""
        result[key] = stats
    return result


def fetch_all_projections() -> dict[str, dict]:
    """Fetch all projection sources, return {system_name: {player_name: {stats}}}."""
    all_projections = {}

    for key, info in PROJECTION_SYSTEMS.items():
        system_type = info["type"]
        label = info["label"]
        logger.info("Fetching %s projections...", label)

        # Fetch hitting and pitching
        hitting_raw = _fetch_fangraphs_projections(system_type, stats="bat")
        pitching_raw = _fetch_fangraphs_projections(system_type, stats="pit")

        hitting = _parse_hitting_projections(hitting_raw)
        pitching = _parse_pitching_projections(pitching_raw)

        # Merge: pitchers get pitching stats, hitters get hitting stats
        merged = {}
        for name, stats in hitting.items():
            merged[name] = {"type": "hitter", **stats}
        for name, stats in pitching.items():
            if name in merged:
                merged[name].update(stats)
                merged[name]["type"] = "both"
            else:
                merged[name] = {"type": "pitcher", **stats}

        all_projections[key] = merged
        logger.info("  %s: %d hitters, %d pitchers", label, len(hitting), len(pitching))

    return all_projections


def get_player_projections(player_name: str, all_projections: dict) -> dict[str, dict]:
    """Look up projections for a specific player across all systems."""
    normalized = _normalize_player_name(player_name)
    result = {}

    for system_key, players in all_projections.items():
        label = PROJECTION_SYSTEMS.get(system_key, {}).get("label", system_key)
        if normalized in players:
            result[label] = players[normalized]
        else:
            # Fuzzy fallback: check last name + first initial
            parts = normalized.split()
            if len(parts) >= 2:
                last = parts[-1]
                for pname, pstats in players.items():
                    pparts = pname.split()
                    if len(pparts) >= 2 and pparts[-1] == last and pparts[0][0] == parts[0][0]:
                        result[label] = pstats
                        break

    return result
