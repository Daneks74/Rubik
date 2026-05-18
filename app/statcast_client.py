"""Fetch Statcast expected stats from Baseball Savant."""

import csv
import io
import logging
import requests

logger = logging.getLogger(__name__)

SAVANT_URL = "https://baseballsavant.mlb.com/leaderboard/expected_statistics"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/csv,*/*",
}


def fetch_expected_stats(year: int, min_pa: int = 25) -> list[dict]:
    """Fetch expected statistics from Baseball Savant.

    Returns list of player dicts with actual and Statcast-expected stats.
    xOBP is estimated as xBA*(1 - bb_rate) + bb_rate, since walks are not
    batted-ball events and are identical in actual vs expected.
    """
    try:
        resp = requests.get(SAVANT_URL, params={
            "type": "batter",
            "year": year,
            "position": "",
            "team": "",
            "min": min_pa,
            "csv": "true",
        }, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Baseball Savant request failed: %s", e)
        return []

    text = resp.text.strip()
    if not text or text.startswith("<!"):
        logger.warning("Baseball Savant returned non-CSV response")
        return []

    reader = csv.DictReader(io.StringIO(text))
    players = []

    for row in reader:
        try:
            pa = int(row.get("pa") or 0)
            ba = float(row.get("ba") or 0)
            xba = float(row.get("xba") or 0)
            slg = float(row.get("slg") or 0)
            xslg = float(row.get("xslg") or 0)
            woba = float(row.get("woba") or 0)
            xwoba = float(row.get("xwoba") or 0)
            bb_pct = float(row.get("bb_percent") or 0)
            k_pct = float(row.get("k_percent") or 0)

            bb_rate = bb_pct / 100.0
            obp = ba * (1 - bb_rate) + bb_rate
            xobp = xba * (1 - bb_rate) + bb_rate

            last = row.get("last_name", "").strip()
            first = row.get("first_name", "").strip()
            name = f"{first} {last}" if first and last else last or first

            players.append({
                "name": name,
                "player_id": row.get("player_id", ""),
                "pa": pa,
                "obp": round(obp, 3),
                "xobp": round(xobp, 3),
                "obp_diff": round(obp - xobp, 3),
                "ba": round(ba, 3),
                "xba": round(xba, 3),
                "ba_diff": round(ba - xba, 3),
                "slg": round(slg, 3),
                "xslg": round(xslg, 3),
                "slg_diff": round(slg - xslg, 3),
                "woba": round(woba, 3),
                "xwoba": round(xwoba, 3),
                "woba_diff": round(woba - xwoba, 3),
                "bb_pct": round(bb_pct, 1),
                "k_pct": round(k_pct, 1),
            })
        except (ValueError, TypeError):
            continue

    players.sort(key=lambda p: abs(p["obp_diff"]), reverse=True)
    return players
