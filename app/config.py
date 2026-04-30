import os
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv


@dataclass
class AppConfig:
    espn_league_ids: list[int] = field(default_factory=list)
    espn_s2: str = ""
    espn_swid: str = ""
    season_year: int = 2026
    odds_api_key: Optional[str] = None
    wizard_backend_url: Optional[str] = None

    @property
    def has_espn(self) -> bool:
        return bool(self.espn_s2 and self.espn_swid and self.espn_league_ids)


def load_config() -> AppConfig:
    load_dotenv()

    # Support both ESPN_LEAGUE_IDS (comma-separated) and ESPN_LEAGUE_ID (single, backward compat)
    league_ids = []
    ids_str = os.getenv("ESPN_LEAGUE_IDS", "")
    if ids_str:
        league_ids = [int(x.strip()) for x in ids_str.split(",") if x.strip()]
    else:
        single_id = os.getenv("ESPN_LEAGUE_ID", "")
        if single_id:
            league_ids = [int(single_id)]

    espn_s2 = os.getenv("ESPN_S2", "")
    espn_swid = os.getenv("ESPN_SWID", "")
    season_year = int(os.getenv("SEASON_YEAR", "2026"))
    odds_api_key = os.getenv("ODDS_API_KEY") or None

    wizard_backend_url = os.getenv("WIZARD_BACKEND_URL") or None

    return AppConfig(
        espn_league_ids=league_ids,
        espn_s2=espn_s2,
        espn_swid=espn_swid,
        season_year=season_year,
        odds_api_key=odds_api_key,
        wizard_backend_url=wizard_backend_url,
    )
