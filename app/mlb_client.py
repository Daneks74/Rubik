import logging
from datetime import date, timedelta
import statsapi
from app.models import ScheduledStart

logger = logging.getLogger(__name__)

# Maps MLB Stats API full team names → ESPN-style abbreviations
TEAM_NAME_TO_ABBREV = {
    "Arizona Diamondbacks": "Ari",
    "Atlanta Braves": "Atl",
    "Baltimore Orioles": "Bal",
    "Boston Red Sox": "Bos",
    "Chicago Cubs": "ChC",
    "Chicago White Sox": "ChW",
    "Cincinnati Reds": "Cin",
    "Cleveland Guardians": "Cle",
    "Colorado Rockies": "Col",
    "Detroit Tigers": "Det",
    "Houston Astros": "Hou",
    "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA",
    "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "Fla",
    "Milwaukee Brewers": "Mil",
    "Minnesota Twins": "Min",
    "New York Mets": "NYM",
    "New York Yankees": "NYY",
    "Oakland Athletics": "Oak",
    "Philadelphia Phillies": "Phi",
    "Pittsburgh Pirates": "Pit",
    "San Diego Padres": "SD",
    "San Francisco Giants": "SF",
    "Seattle Mariners": "Sea",
    "St. Louis Cardinals": "StL",
    "Tampa Bay Rays": "TB",
    "Texas Rangers": "Tex",
    "Toronto Blue Jays": "Tor",
    "Washington Nationals": "Wsh",
}

# Reverse: abbreviation → full name
ABBREV_TO_TEAM_NAME = {v: k for k, v in TEAM_NAME_TO_ABBREV.items()}


def get_probable_starters(start_date: date, days_ahead: int = 7) -> list[ScheduledStart]:
    """Fetch probable starting pitchers for a date range."""
    results = []
    end_date = start_date + timedelta(days=days_ahead)

    current = start_date
    while current <= end_date:
        try:
            sched = statsapi.schedule(
                date=current.strftime("%m/%d/%Y"),
                sportId=1,
            )
        except Exception as e:
            logger.warning("Failed to fetch schedule for %s: %s", current, e)
            current += timedelta(days=1)
            continue

        for game in sched:
            for side in ("home", "away"):
                pitcher_key = f"{side}_probable_pitcher"
                pitcher_name = game.get(pitcher_key, "")
                if not pitcher_name or pitcher_name == "":
                    continue

                team_key = f"{side}_name"
                opp_side = "away" if side == "home" else "home"
                opp_key = f"{opp_side}_name"

                team_name = game.get(team_key, "")
                opp_name = game.get(opp_key, "")

                team_abbrev = TEAM_NAME_TO_ABBREV.get(team_name, "???")
                opp_abbrev = TEAM_NAME_TO_ABBREV.get(opp_name, "???")

                results.append(ScheduledStart(
                    pitcher_name=pitcher_name,
                    team_name=team_name,
                    team_abbrev=team_abbrev,
                    opponent_name=opp_name,
                    opponent_abbrev=opp_abbrev,
                    game_date=current,
                    game_id=game.get("game_id", 0),
                    is_home=(side == "home"),
                ))

        current += timedelta(days=1)

    return results


def get_team_records() -> dict[str, dict]:
    """Fetch current MLB standings/records. Returns {team_abbrev: {wins, losses, pct}}."""
    records = {}
    try:
        data = statsapi.standings_data(leagueId="103,104")
    except Exception as e:
        logger.warning("Failed to fetch MLB standings: %s", e)
        return records

    for div_id, div_data in data.items():
        for team in div_data.get("teams", []):
            name = team.get("name", "")
            abbrev = TEAM_NAME_TO_ABBREV.get(name, "")
            if abbrev:
                w = team.get("w", 0)
                l = team.get("l", 0)
                pct = w / (w + l) if (w + l) > 0 else 0.5
                records[abbrev] = {"wins": w, "losses": l, "pct": round(pct, 3)}

    return records
