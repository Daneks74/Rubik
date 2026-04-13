import logging
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import load_config, AppConfig
from app.espn_client import ESPNClient, STAT_ID_MAP
from app.mlb_client import get_probable_starters, get_team_records
from app.odds_client import get_mlb_odds
from app.projections_client import fetch_all_projections, get_player_projections, _normalize_player_name
from app.matcher import match_pitchers_to_starts, match_start_to_odds
from app.scorer import score_pitcher, rank_recommendations

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Fantasy Baseball Dashboard")

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# ── Global state ──
_config: AppConfig | None = None
_espn_clients: dict[int, ESPNClient] = {}
_active_league_id: int | None = None
_projections_cache: dict | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def get_espn() -> ESPNClient | None:
    """Get the active league's ESPN client, or None if not configured."""
    global _active_league_id
    config = get_config()
    if not config.has_espn:
        return None

    if _active_league_id is None and config.espn_league_ids:
        _active_league_id = config.espn_league_ids[0]

    if _active_league_id is None:
        return None

    if _active_league_id not in _espn_clients:
        _espn_clients[_active_league_id] = ESPNClient.connect(_active_league_id, config)

    return _espn_clients[_active_league_id]


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
# API: App status & mode
# ──────────────────────────────────────────────

@app.get("/api/status")
async def api_status():
    config = get_config()
    return {
        "has_espn": config.has_espn,
        "has_odds": bool(config.odds_api_key),
        "league_ids": config.espn_league_ids,
        "active_league_id": _active_league_id,
    }


# ──────────────────────────────────────────────
# API: Leagues (multi-league support)
# ──────────────────────────────────────────────

@app.get("/api/leagues")
async def api_leagues():
    config = get_config()
    if not config.has_espn:
        return {"leagues": [], "active_id": None, "has_espn": False}

    leagues = []
    for lid in config.espn_league_ids:
        if lid not in _espn_clients:
            _espn_clients[lid] = ESPNClient.connect(lid, config)
        client = _espn_clients[lid]
        try:
            name = client.get_league_name()
            num_teams = len(client.league.teams)
        except Exception as e:
            name = f"League {lid}"
            num_teams = 0
            logger.warning("Failed to connect to league %s: %s", lid, e)
        leagues.append({"id": lid, "name": name, "num_teams": num_teams})

    return {
        "leagues": leagues,
        "active_id": _active_league_id or (config.espn_league_ids[0] if config.espn_league_ids else None),
        "has_espn": True,
    }


@app.post("/api/switch-league")
async def api_switch_league(league_id: int = Query(...)):
    global _active_league_id
    config = get_config()
    if league_id not in config.espn_league_ids:
        return JSONResponse(status_code=400, content={"error": f"League {league_id} not configured"})
    _active_league_id = league_id
    return {"status": "ok", "active_league_id": league_id}


# ──────────────────────────────────────────────
# API: League info
# ──────────────────────────────────────────────

@app.get("/api/league")
async def api_league_info():
    espn = get_espn()
    if not espn:
        return {"settings": {"league_name": "SP Tracker (No League)", "scoring_type": "none"}, "categories": []}

    try:
        settings = espn.get_league_settings()
        categories = espn.get_stat_categories()
        return {"settings": settings, "categories": categories}
    except Exception as e:
        logger.exception("League info error")
        return JSONResponse(status_code=500, content={"error": f"ESPN API error: {e}"})


# ──────────────────────────────────────────────
# API: SP Picker (works with OR without ESPN)
# ──────────────────────────────────────────────

@app.get("/api/sp-picker")
async def api_sp_picker():
    config = get_config()
    espn = get_espn()

    today = date.today()
    starts = get_probable_starters(today, days_ahead=7)
    records = get_team_records()

    odds_list = []
    if config.odds_api_key:
        odds_list = get_mlb_odds(config.odds_api_key)

    all_proj = get_projections()

    if espn:
        # Full mode: show free agent SPs with scheduled starts
        free_agents = espn.get_free_agent_sps(size=150)
        matched = match_pitchers_to_starts(free_agents, starts)
        recs = []
        for pitcher, start in matched:
            odds = match_start_to_odds(start, odds_list) if odds_list else None
            rec = score_pitcher(pitcher, start, odds, free_agents, team_records=records)
            recs.append(rec)
        recs = rank_recommendations(recs)
        mode = "league"
    else:
        # No-ESPN mode: show ALL probable starters with projections + odds
        recs = []
        from app.models import PlayerInfo, PitcherRecommendation
        for start in starts:
            # Build a pseudo-PlayerInfo from FanGraphs projections
            proj = get_player_projections(start.pitcher_name, all_proj)
            espn_proj = proj.get("ESPN", {})
            steamer = proj.get("Steamer", {})
            best_proj = steamer or espn_proj or {}

            pitcher = PlayerInfo(
                espn_id=0,
                name=start.pitcher_name,
                pro_team=start.team_abbrev,
                position="SP",
                percent_owned=0,
                projected_points=0,
                stats=best_proj,
                projected_stats=best_proj,
            )

            odds = match_start_to_odds(start, odds_list) if odds_list else None

            # Simple scoring: team record + home advantage + odds
            from app.scorer import score_pitcher as sp
            rec = sp(pitcher, start, odds, [], team_records=records)
            recs.append(rec)

        recs = rank_recommendations(recs)
        mode = "public"

    # Group by date
    by_date = {}
    for rec in recs:
        d = rec.scheduled_start.game_date.isoformat()
        if d not in by_date:
            by_date[d] = []

        opp_record = records.get(rec.scheduled_start.opponent_abbrev, {})
        pitcher_record = records.get(rec.pitcher.pro_team, {})

        # Get FanGraphs projections for this pitcher
        fg_proj = get_player_projections(rec.pitcher.name, all_proj)
        steamer = fg_proj.get("Steamer", {})
        zips = fg_proj.get("ZiPS", {})

        entry = {
            "name": rec.pitcher.name,
            "team": rec.pitcher.pro_team,
            "opponent": rec.scheduled_start.opponent_abbrev,
            "is_home": rec.scheduled_start.is_home,
            "projected_pts": rec.pitcher.projected_points or None,
            "pct_owned": rec.pitcher.percent_owned,
            "moneyline": (rec.odds.home_moneyline if rec.scheduled_start.is_home else rec.odds.away_moneyline) if rec.odds else None,
            "win_prob": round(rec.win_probability * 100, 1) if rec.win_probability else None,
            "over_under": rec.odds.over_under if rec.odds else None,
            "team_record": f"{pitcher_record.get('wins', 0)}-{pitcher_record.get('losses', 0)}" if pitcher_record else None,
            "opp_record": f"{opp_record.get('wins', 0)}-{opp_record.get('losses', 0)}" if opp_record else None,
            "opp_win_pct": opp_record.get("pct"),
            "score": rec.score,
            "breakdown": rec.score_breakdown,
            # FanGraphs stats for no-ESPN mode
            "era": steamer.get("ERA") or zips.get("ERA"),
            "whip": steamer.get("WHIP") or zips.get("WHIP"),
            "k9": steamer.get("K/9") or zips.get("K/9"),
        }
        by_date[d].append(entry)

    return {
        "dates": by_date,
        "total_free_agents": len(free_agents) if espn else 0,
        "total_with_starts": len(recs),
        "has_odds": bool(odds_list),
        "has_records": bool(records),
        "mode": mode,
    }


# ──────────────────────────────────────────────
# API: League Rankings (requires ESPN)
# ──────────────────────────────────────────────

@app.get("/api/rankings")
async def api_rankings(mode: str = Query("current", enum=["current", "projected"])):
    espn = get_espn()
    if not espn:
        return JSONResponse(status_code=400, content={"error": "ESPN league not configured. Add ESPN_S2 and ESPN_SWID to enable this feature."})

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
                    if isinstance(sid_str, str) and not sid_str.isdigit():
                        continue
                    sid = int(sid_str) if isinstance(sid_str, str) else sid_str
                    sname = STAT_ID_MAP.get(sid, "")
                    if sname in cat_names:
                        team_stats[sname] = team_stats.get(sname, 0) + val

                for sid_str, val in proj_breakdown.items():
                    if isinstance(sid_str, str) and not sid_str.isdigit():
                        continue
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

    for t in teams_data:
        ranks = t.get("ranks", {})
        t["total_rank_score"] = sum(ranks.values())

    teams_data.sort(key=lambda t: t["total_rank_score"])
    for i, t in enumerate(teams_data, 1):
        t["overall_rank"] = i

    return {"teams": teams_data, "categories": categories, "mode": mode}


# ──────────────────────────────────────────────
# API: My Roster (requires ESPN)
# ──────────────────────────────────────────────

@app.get("/api/my-roster")
async def api_my_roster(team_name: str = Query(None)):
    espn = get_espn()
    if not espn:
        return JSONResponse(status_code=400, content={"error": "ESPN league not configured. Add ESPN_S2 and ESPN_SWID to enable this feature."})

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
                    if val <= pct["p25"]: grades[stat_name] = "elite"
                    elif val <= pct["p50"]: grades[stat_name] = "good"
                    elif val <= pct["p75"]: grades[stat_name] = "avg"
                    else: grades[stat_name] = "poor"
                else:
                    if val >= pct["p75"]: grades[stat_name] = "elite"
                    elif val >= pct["p50"]: grades[stat_name] = "good"
                    elif val >= pct["p25"]: grades[stat_name] = "avg"
                    else: grades[stat_name] = "poor"

        player_list.append({
            "name": p.name, "position": p.position, "team": p.pro_team,
            "eligible_slots": p.eligible_slots, "injury_status": p.injury_status,
            "pct_owned": p.percent_owned,
            "current_stats": p.stats, "projected_stats": p.projected_stats,
            "projections": projections,
            "total_points": p.total_points, "projected_points": p.projected_points,
            "grades": grades,
        })

    return {
        "team_name": team_data["team_name"],
        "players": player_list,
        "categories": categories,
        "needs_selection": False,
    }


# ──────────────────────────────────────────────
# API: Free Agents (requires ESPN)
# ──────────────────────────────────────────────

@app.get("/api/free-agents")
async def api_free_agents(
    position: str = Query(None),
    stat_view: str = Query("current", enum=["current", "projected"]),
    limit: int = Query(50, ge=10, le=200),
):
    espn = get_espn()
    if not espn:
        return JSONResponse(status_code=400, content={"error": "ESPN league not configured. Add ESPN_S2 and ESPN_SWID to enable this feature."})

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
            "name": p.name, "position": p.position, "team": p.pro_team,
            "eligible_slots": p.eligible_slots, "injury_status": p.injury_status,
            "pct_owned": p.percent_owned, "pct_started": p.percent_started,
            "stats": stats, "projections": projections,
            "total_points": p.total_points, "projected_points": p.projected_points,
        })

    return {"players": player_list, "categories": categories, "position": position, "stat_view": stat_view}


# ──────────────────────────────────────────────
# API: Positions + Refresh
# ──────────────────────────────────────────────

@app.get("/api/positions")
async def api_positions():
    return {"positions": ["C", "1B", "2B", "3B", "SS", "OF", "DH", "SP", "RP", "P"]}


@app.post("/api/refresh-projections")
async def api_refresh_projections():
    global _projections_cache
    _projections_cache = None
    get_projections()
    return {"status": "ok", "sources": list((_projections_cache or {}).keys())}


@app.post("/api/refresh-espn")
async def api_refresh_espn():
    global _espn_clients
    _espn_clients = {}
    return {"status": "ok"}


@app.get("/api/debug/team")
async def api_debug_team():
    """Temporary debug endpoint to inspect ESPN team data structure."""
    espn = get_espn()
    if not espn:
        return {"error": "no espn"}
    team = espn.league.teams[0]
    result = {
        "team_name": team.team_name,
        "wins": team.wins,
        "losses": team.losses,
        "ties": getattr(team, 'ties', 0),
        "standing": team.standing,
        "attrs": [a for a in dir(team) if not a.startswith('_')],
    }
    # Check for team-level stats
    for attr in ['stats', 'stats_2025', 'team_stats', 'cumulative_score']:
        val = getattr(team, attr, 'NOT_FOUND')
        if val != 'NOT_FOUND':
            result[f'team.{attr}'] = str(val)[:500]

    # Check first player's stats structure
    if team.roster:
        p = team.roster[0]
        result["player0_name"] = p.name
        result["player0_stats_keys"] = list(p.stats.keys()) if hasattr(p, 'stats') and p.stats else []
        if p.stats:
            first_key = list(p.stats.keys())[0]
            period = p.stats[first_key]
            result["player0_first_period_key"] = first_key
            result["player0_first_period_keys"] = list(period.keys()) if isinstance(period, dict) else str(type(period))
            bd = period.get("breakdown", {})
            result["player0_breakdown_sample"] = dict(list(bd.items())[:10]) if bd else "empty"
    return result
