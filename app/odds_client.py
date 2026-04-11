import logging
from typing import Optional
import requests
from app.models import GameOdds

logger = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/"


def moneyline_to_implied_prob(ml: int) -> float:
    """Convert American moneyline to implied probability (0-1)."""
    if ml < 0:
        return abs(ml) / (abs(ml) + 100)
    else:
        return 100 / (ml + 100)


def get_mlb_odds(api_key: str) -> list[GameOdds]:
    """Fetch MLB odds from The Odds API."""
    if not api_key:
        return []

    try:
        resp = requests.get(BASE_URL, params={
            "apiKey": api_key,
            "regions": "us",
            "markets": "h2h,totals",
            "oddsFormat": "american",
            "dateFormat": "iso",
        }, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Odds API request failed: %s", e)
        return []

    remaining = resp.headers.get("x-requests-remaining")
    if remaining:
        logger.info("Odds API requests remaining: %s", remaining)

    results = []
    for event in resp.json():
        home_team = event.get("home_team", "")
        away_team = event.get("away_team", "")
        commence = event.get("commence_time", "")

        home_ml: Optional[int] = None
        away_ml: Optional[int] = None
        over_under: Optional[float] = None

        bookmakers = event.get("bookmakers", [])
        if not bookmakers:
            continue

        # Use the first bookmaker with data
        for bm in bookmakers:
            markets = {m["key"]: m for m in bm.get("markets", [])}

            if "h2h" in markets and home_ml is None:
                for outcome in markets["h2h"].get("outcomes", []):
                    if outcome["name"] == home_team:
                        home_ml = int(outcome["price"])
                    elif outcome["name"] == away_team:
                        away_ml = int(outcome["price"])

            if "totals" in markets and over_under is None:
                for outcome in markets["totals"].get("outcomes", []):
                    if outcome["name"] == "Over":
                        over_under = float(outcome.get("point", 0))
                        break

            if home_ml is not None and over_under is not None:
                break

        home_prob = moneyline_to_implied_prob(home_ml) if home_ml else None
        away_prob = moneyline_to_implied_prob(away_ml) if away_ml else None

        results.append(GameOdds(
            home_team=home_team,
            away_team=away_team,
            commence_time=commence,
            home_moneyline=home_ml,
            away_moneyline=away_ml,
            over_under=over_under,
            home_implied_prob=round(home_prob, 3) if home_prob else None,
            away_implied_prob=round(away_prob, 3) if away_prob else None,
        ))

    return results
