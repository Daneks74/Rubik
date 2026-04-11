import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv


@dataclass
class AppConfig:
    espn_league_id: int
    espn_s2: str
    espn_swid: str
    season_year: int
    odds_api_key: Optional[str] = None


def load_config() -> AppConfig:
    load_dotenv()

    league_id = os.getenv("ESPN_LEAGUE_ID")
    if not league_id:
        raise ValueError("ESPN_LEAGUE_ID is required in .env")

    espn_s2 = os.getenv("ESPN_S2", "")
    espn_swid = os.getenv("ESPN_SWID", "")
    if not espn_s2 or not espn_swid:
        raise ValueError("ESPN_S2 and ESPN_SWID cookies are required for private leagues. "
                         "Get them from browser DevTools > Application > Cookies > espn.com")

    season_year = int(os.getenv("SEASON_YEAR", "2026"))
    odds_api_key = os.getenv("ODDS_API_KEY") or None

    return AppConfig(
        espn_league_id=int(league_id),
        espn_s2=espn_s2,
        espn_swid=espn_swid,
        season_year=season_year,
        odds_api_key=odds_api_key,
    )
