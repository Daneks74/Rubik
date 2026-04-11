"""Cross-reference players between ESPN, MLB Stats API, and The Odds API."""

import unicodedata
from difflib import SequenceMatcher
from datetime import date
from typing import Optional
from app.models import PlayerInfo, ScheduledStart, GameOdds, PitcherRecommendation
from app.mlb_client import TEAM_NAME_TO_ABBREV


def normalize_name(name: str) -> str:
    """Normalize a player name for cross-API matching."""
    name = unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode("ascii")
    name = name.lower().strip()
    name = name.replace(".", "")
    for suffix in [" jr", " sr", " iii", " ii", " iv"]:
        if name.endswith(suffix):
            name = name[:-len(suffix)].rstrip()
    return name


def extract_last_name(normalized: str) -> str:
    parts = normalized.split()
    return parts[-1] if parts else normalized


def fuzzy_match(a: str, b: str, threshold: float = 0.85) -> bool:
    return SequenceMatcher(None, a, b).ratio() >= threshold


def match_pitchers_to_starts(
    free_agents: list[PlayerInfo],
    starts: list[ScheduledStart],
) -> list[tuple[PlayerInfo, ScheduledStart]]:
    """Match ESPN free agent pitchers to their scheduled starts."""
    matched = []
    used_starts = set()

    for fa in free_agents:
        fa_norm = normalize_name(fa.name)
        fa_team = fa.pro_team
        fa_last = extract_last_name(fa_norm)

        for i, start in enumerate(starts):
            if i in used_starts:
                continue

            start_norm = normalize_name(start.pitcher_name)
            start_team = start.team_abbrev

            # Tier 1: Exact name match
            if fa_norm == start_norm:
                matched.append((fa, start))
                used_starts.add(i)
                continue

            # Tier 2: Same team + last name match
            if fa_team == start_team and fa_last == extract_last_name(start_norm):
                matched.append((fa, start))
                used_starts.add(i)
                continue

            # Tier 3: Same team + fuzzy full name
            if fa_team == start_team and fuzzy_match(fa_norm, start_norm):
                matched.append((fa, start))
                used_starts.add(i)
                continue

    return matched


# Build a reverse lookup for odds: full team name → abbreviation
_ODDS_TEAM_TO_ABBREV = dict(TEAM_NAME_TO_ABBREV)


def match_start_to_odds(
    start: ScheduledStart,
    odds_list: list[GameOdds],
) -> Optional[GameOdds]:
    """Find odds for a scheduled start by matching teams."""
    for odds in odds_list:
        home_abbrev = _ODDS_TEAM_TO_ABBREV.get(odds.home_team, "")
        away_abbrev = _ODDS_TEAM_TO_ABBREV.get(odds.away_team, "")

        if start.is_home and start.team_abbrev == home_abbrev and start.opponent_abbrev == away_abbrev:
            return odds
        if not start.is_home and start.team_abbrev == away_abbrev and start.opponent_abbrev == home_abbrev:
            return odds

    return None
