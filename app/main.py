import logging
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import load_config, AppConfig
from app.espn_client import ESPNClient, STAT_ID_MAP
from app.mlb_client import get_probable_starters
from app.odds_client import get_mlb_odds
from app.projections_client import fetch_all_projections, get_player_projections
from app.matcher import match_pitchers_to_starts, match_start_to_odds
from app.scorer import score_pitcher, rank_recommendations

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Fantasy Baseball Dashboard")

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Global state (lazy-loaded)
_config: AppConfig | None = None
_espn: ESPNClient | None = None
_projections_cache: dict | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def get_espn() -> ESPNClient:
    global _espn
    if _espn is None:
        _espn = ESPNClient(get_config())
    return _espn


def get_projections() -> dict:
    global _projections_cache
    if _projections_cache is None:
        _projections_cache = fetch_all_projections()
    return _projections_cache


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"error": str(exc)})


# ──────────────────────────────────────────────
# Pages
# ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


# ──────────────────────────────────────────────
# API: League info
# ──────────────────────────────────────────────

@app.get("/api/league")
async def api_league_info():
    try:
        espn = get_espn()
        settings = espn.get_league_settings()
        categories = espn.get_stat_categories()
        return {"settings": settings, "categories": categories}
    except ValueError as e:
        return JSONResponse(status_code=400, content={
            "error": str(e),
            "setup_required": True,
            "instructions": "Copy .env.example to .env and fill in your ESPN credentials."
        })
    except Exception as e:
        logger.exception("League info error")
        return JSONResponse(status_code=500, content={"error": f"ESPN API error: {e}"})


# ──────────────────────────────────────────────
# API: SP Picker
# ──────────────────────────────────────────────

@app.get("/api/sp-picker")
async def api_sp_picker():
    config = get_config()
    espn = get_espn()

    free_agents = espn.get_free_agent_sps(size=150)

    today = date.today()
    starts = get_probable_starters(today, days_ahead=7)

    odds_list = []
    if config.odds_api_key:
        odds_list = get_mlb_odds(config.odds_api_key)

    matched = match_pitchers_to_starts(free_agents, starts)
    recs = []
    for pitcher, start in matched:
        odds = match_start_to_odds(start, odds_list) if odds_list else None
        rec = score_pitcher(pitcher, start, odds, free_agents)
        recs.append(rec)

    recs = rank_recommendations(recs)

    by_date = {}
    for rec in recs:
        d = rec.scheduled_start.game_date.isoformat()
        if d not in by_date:
            by_date[d] = []
        by_date[d].append({
            "name": rec.pitcher.name,
            "team": rec.pitcher.pro_team,
            "opponent": rec.scheduled_start.opponent_abbrev,
            "is_home": rec.scheduled_start.is_home,
            "projected_pts": rec.pitcher.projected_points,
            "pct_owned": rec.pitcher.percent_owned,
            "moneyline": (rec.odds.home_moneyline if rec.scheduled_start.is_home else rec.odds.away_moneyline) if rec.odds else None,
            "win_prob": round(rec.win_probability * 100, 1) if rec.win_probability else None,
            "over_under": rec.odds.over_under if rec.odds else None,
            "score": rec.score,
            "breakdown": rec.score_breakdown,
        })

    return {
        "dates": by_date,
        "total_free_agents": len(free_agents),
        "total_with_starts": len(recs),
        "has_odds": bool(odds_list),
    }


# ──────────────────────────────────────────────
# API: League Rankings
# ──────────────────────────────────────────────

@app.get("/api/rankings")
async def api_rankings(mode: str = Query("current", enum=["current", "projected"])):
    espn = get_espn()
    categories = espn.get_stat_categories()
    cat_names = [c["name"] for c in categories]
    inverse_cats = {c["name"] for c in categories if c.get("is_inverse")}

    teams_data = []
    for team in espn.league.teams:
        team_stats = {}
        team_projected = {}

        for player in team.roster:
            if not player.stats:
                continue
            for period_id, period_data in player.stats.items():
                breakdown = period_data.get("breakdown", {})
                proj_breakdown = period_data.get("projected_breakdown", {})

                for sid_str, val in breakdown.items():
                    sid = int(sid_str) if isinstance(sid_str, str) else sid_str
                    sname = STAT_ID_MAP.get(sid, "")
                    if sname in cat_names:
                        team_stats[sname] = team_stats.get(sname, 0) + val

                for sid_str, val in proj_breakdown.items():
                    sid = int(sid_str) if isinstance(sid_str, str) else sid_str
                    sname = STAT_ID_MAP.get(sid, "")
                    if sname in cat_names:
                        team_projected[sname] = team_projected.get(sname, 0) + val

        teams_data.append({
            "team_id": team.team_id,
            "team_name": team.team_name,
            "team_abbrev": team.team_abbrev,
            "logo_url": getattr(team, 'logo_url', ''),
            "standing": team.standing,
            "wins": team.wins,
            "losses": team.losses,
            "current_stats": team_stats,
            "projected_stats": team_projected,
        })

    # Rank teams per category
    source = "current_stats" if mode == "current" else "projected_stats"
    for cat in cat_names:
        is_inv = cat in inverse_cats
        sorted_teams = sorted(
            teams_data,
            key=lambda t: t[source].get(cat, float('inf') if is_inv else 0),
            reverse=not is_inv,
        )
        for rank, t in enumerate(sorted_teams, 1):
            if "ranks" not in t:
                t["ranks"] = {}
            t["ranks"][cat] = rank

    # Overall rank (sum of category ranks, lower = better)
    for t in teams_data:
        ranks = t.get("ranks", {})
        t["total_rank_score"] = sum(ranks.values())

    teams_data.sort(key=lambda t: t["total_rank_score"])
    for i, t in enumerate(teams_data, 1):
        t["overall_rank"] = i

    return {
        "teams": teams_data,
        "categories": categories,
        "mode": mode,
    }


# ──────────────────────────────────────────────
# API: My Roster
# ──────────────────────────────────────────────

@app.get("/api/my-roster")
async def api_my_roster(team_name: str = Query(None)):
    espn = get_espn()
    categories = espn.get_stat_categories()
    cat_names = [c["name"] for c in categories]
    inverse_cats = {c["name"] for c in categories if c.get("is_inverse")}

    if team_name is None:
        return {
            "teams": [{"id": t.team_id, "name": t.team_name} for t in espn.league.teams],
            "needs_selection": True,
        }

    team_data = espn.get_my_team(team_name)
    players = team_data["players"]

    all_proj = get_projections()

    # Compute league-wide percentile thresholds for color coding
    all_players = []
    for team in espn.league.teams:
        for p in team.roster:
            info = espn._extract_player_info(p)
            all_players.append(info)

    stat_values = {}
    for p in all_players:
        for stat_name, val in p.stats.items():
            if stat_name in cat_names and isinstance(val, (int, float)):
                if stat_name not in stat_values:
                    stat_values[stat_name] = []
                stat_values[stat_name].append(val)

    stat_percentiles = {}
    for sname, vals in stat_values.items():
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        if n > 0:
            stat_percentiles[sname] = {
                "p25": vals_sorted[max(0, n // 4)],
                "p50": vals_sorted[n // 2],
                "p75": vals_sorted[max(0, 3 * n // 4)],
            }

    player_list = []
    for p in players:
        projections = get_player_projections(p.name, all_proj)
        if p.projected_stats:
            projections["ESPN"] = p.projected_stats

        grades = {}
        for stat_name, val in p.stats.items():
            if stat_name in stat_percentiles and isinstance(val, (int, float)):
                pct = stat_percentiles[stat_name]
                is_inv = stat_name in inverse_cats
                if is_inv:
                    if val <= pct["p25"]:
                        grades[stat_name] = "elite"
                    elif val <= pct["p50"]:
                        grades[stat_name] = "good"
                    elif val <= pct["p75"]:
                        grades[stat_name] = "avg"
                    else:
                        grades[stat_name] = "poor"
                else:
                    if val >= pct["p75"]:
                        grades[stat_name] = "elite"
                    elif val >= pct["p50"]:
                        grades[stat_name] = "good"
                    elif val >= pct["p25"]:
                        grades[stat_name] = "avg"
                    else:
                        grades[stat_name] = "poor"

        player_list.append({
            "name": p.name,
            "position": p.position,
            "team": p.pro_team,
            "eligible_slots": p.eligible_slots,
            "injury_status": p.injury_status,
            "pct_owned": p.percent_owned,
            "current_stats": p.stats,
            "projected_stats": p.projected_stats,
            "projections": projections,
            "total_points": p.total_points,
            "projected_points": p.projected_points,
            "grades": grades,
        })

    return {
        "team_name": team_data["team_name"],
        "players": player_list,
        "categories": categories,
        "needs_selection": False,
    }


# ──────────────────────────────────────────────
# API: Free Agents
# ──────────────────────────────────────────────

@app.get("/api/free-agents")
async def api_free_agents(
    position: str = Query(None),
    stat_view: str = Query("current", enum=["current", "projected"]),
    limit: int = Query(50, ge=10, le=200),
):
    espn = get_espn()
    categories = espn.get_stat_categories()

    agents = espn.get_free_agents(size=limit, position=position)

    all_proj = get_projections()

    player_list = []
    for p in agents:
        projections = get_player_projections(p.name, all_proj)
        if p.projected_stats:
            projections["ESPN"] = p.projected_stats

        stats = p.projected_stats if stat_view == "projected" else p.stats

        player_list.append({
            "name": p.name,
            "position": p.position,
            "team": p.pro_team,
            "eligible_slots": p.eligible_slots,
            "injury_status": p.injury_status,
            "pct_owned": p.percent_owned,
            "pct_started": p.percent_started,
            "stats": stats,
            "projections": projections,
            "total_points": p.total_points,
            "projected_points": p.projected_points,
        })

    return {
        "players": player_list,
        "categories": categories,
        "position": position,
        "stat_view": stat_view,
    }


# ──────────────────────────────────────────────
# API: Available positions
# ──────────────────────────────────────────────

@app.get("/api/positions")
async def api_positions():
    return {
        "positions": ["C", "1B", "2B", "3B", "SS", "OF", "DH", "SP", "RP", "P"]
    }


# ──────────────────────────────────────────────
# API: Refresh caches
# ──────────────────────────────────────────────

@app.post("/api/refresh-projections")
async def api_refresh_projections():
    global _projections_cache
    _projections_cache = None
    get_projections()
    return {"status": "ok", "sources": list((_projections_cache or {}).keys())}


@app.post("/api/refresh-espn")
async def api_refresh_espn():
    global _espn
    _espn = None
    get_espn()
    return {"status": "ok"}
