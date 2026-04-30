import logging
from datetime import date, timedelta
from pathlib import Path

import requests as http_requests

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import load_config, AppConfig
from app.espn_client import ESPNClient, INVERSE_STATS
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
# Backend projection integration
# ──────────────────────────────────────────────

def _norm_pitcher_name(name: str) -> str:
    return name.lower().replace(".", "").replace("-", " ").strip()


def _fetch_backend_projections(dates: list[date]) -> dict[str, dict]:
    """Fetch per-game projections from the backend for the given dates.
    Returns a dict mapping normalized pitcher name → projection dict.
    Fails gracefully to an empty dict if the backend is unreachable."""
    config = get_config()
    if not config.wizard_backend_url:
        return {}

    proj_map: dict[str, dict] = {}
    for d in dates[:3]:
        try:
            resp = http_requests.get(
                f"{config.wizard_backend_url}/projections/tomorrow",
                params={"date": d.isoformat(), "limit": 100},
                timeout=8,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            for p in data.get("pitchers", []):
                key = _norm_pitcher_name(p["pitcher_name"])
                proj_map[key] = p
        except Exception as e:
            logger.warning("Backend projection fetch failed for %s: %s", d, e)
            break
    return proj_map


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

    # Fetch matchup-adjusted per-game projections from the backend
    tomorrow = today + timedelta(days=1)
    backend_projs = _fetch_backend_projections([today, tomorrow])

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

        # FanGraphs ROS stats (season-level averages)
        ros_era = steamer.get("ERA") or zips.get("ERA")
        ros_whip = steamer.get("WHIP") or zips.get("WHIP")

        # ESPN stats from the player object (current season + projected)
        espn_era = rec.pitcher.projected_stats.get("ERA") or rec.pitcher.stats.get("ERA")
        espn_whip = rec.pitcher.projected_stats.get("WHIP") or rec.pitcher.stats.get("WHIP")

        # Backend per-game projection (matchup-adjusted)
        bp = backend_projs.get(_norm_pitcher_name(rec.pitcher.name))

        # Cascade: backend per-game > FanGraphs ROS > ESPN
        best_era = bp["projected_era"] if bp else (ros_era or espn_era)
        best_whip = bp["projected_whip"] if bp else (ros_whip or espn_whip)

        # Log first pitcher's available data for debugging
        if not by_date:
            stat_keys = sorted(rec.pitcher.stats.keys()) if rec.pitcher.stats else []
            proj_keys = sorted(rec.pitcher.projected_stats.keys()) if rec.pitcher.projected_stats else []
            logger.info(
                "SP Picker debug — %s: stats_keys=%s, proj_keys=%s, "
                "espn_era=%s, espn_whip=%s, ros_era=%s, ros_whip=%s, backend=%s",
                rec.pitcher.name, stat_keys[:10], proj_keys[:10],
                espn_era, espn_whip, ros_era, ros_whip, bool(bp),
            )

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
            # Best available ERA/WHIP: backend per-game > FanGraphs ROS > ESPN
            "era": best_era,
            "whip": best_whip,
            "k9": steamer.get("K/9") or zips.get("K/9"),
            # Per-game projection details (when backend data is available)
            "game_proj": bool(bp),
            "game_k": bp["projected_k"] if bp else None,
            "game_ip": bp["projected_ip"] if bp else None,
            "confidence": bp.get("confidence") if bp else None,
        }
        by_date[d].append(entry)

    return {
        "dates": by_date,
        "total_free_agents": len(free_agents) if espn else 0,
        "total_with_starts": len(recs),
        "has_odds": bool(odds_list),
        "has_records": bool(records),
        "has_backend": bool(backend_projs),
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

    # Rate stats that shouldn't be summed across players
    rate_stats = {"AVG", "OBP", "OPS", "SLG", "ERA", "WHIP", "K/9", "K/BB", "OBA", "OOBP", "WPCT", "SV%"}

    teams_data = []
    for team in espn.league.teams:
        team_stats = {}
        team_projected = {}

        for player in team.roster:
            if not hasattr(player, 'stats') or not player.stats:
                continue
            # Period 0 = season totals
            period = player.stats.get(0, {})
            breakdown = period.get("breakdown", {})
            proj_breakdown = period.get("projected_breakdown", {})

            for key, val in breakdown.items():
                if key in rate_stats:
                    continue  # Handle rate stats separately
                if key in cat_names:
                    team_stats[key] = team_stats.get(key, 0) + val

            for key, val in proj_breakdown.items():
                if key in rate_stats:
                    continue
                if key in cat_names:
                    team_projected[key] = team_projected.get(key, 0) + val

        # Calculate rate stats from components
        # AVG = H / AB, OBP = (H+BB+HBP)/(PA), ERA = ER*27/OUTS, WHIP = (P_H+P_BB)*3/OUTS
        _calc_rate_stats(team_stats, team, espn, period_key=0, is_projected=False)
        _calc_rate_stats(team_projected, team, espn, period_key=0, is_projected=True)

        teams_data.append({
            "team_id": team.team_id,
            "team_name": team.team_name,
            "team_abbrev": team.team_abbrev,
            "logo_url": getattr(team, 'logo_url', ''),
            "standing": team.standing,
            "wins": team.wins,
            "losses": team.losses,
            "ties": getattr(team, 'ties', 0),
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


def _calc_rate_stats(stats: dict, team, espn, period_key=0, is_projected=False):
    """Calculate rate stats (AVG, ERA, WHIP, etc.) from component totals."""
    totals = {"AB": 0, "H": 0, "PA": 0, "B_BB": 0, "HBP": 0,
              "OUTS": 0, "ER": 0, "P_H": 0, "P_BB": 0}

    bd_key = "projected_breakdown" if is_projected else "breakdown"
    for player in team.roster:
        if not hasattr(player, 'stats') or not player.stats:
            continue
        period = player.stats.get(period_key, {})
        bd = period.get(bd_key, {})
        for k in totals:
            totals[k] += bd.get(k, 0)

    if totals["AB"] > 0:
        stats["AVG"] = round(totals["H"] / totals["AB"], 5)
    if totals["PA"] > 0:
        stats["OBP"] = round((totals["H"] + totals["B_BB"] + totals["HBP"]) / totals["PA"], 5)
    if totals["AB"] > 0:
        slg = stats.get("TB", 0) / totals["AB"] if totals["AB"] > 0 else 0
        stats["SLG"] = round(slg, 5)
        stats["OPS"] = round(stats.get("OBP", 0) + slg, 5)
    if totals["OUTS"] > 0:
        stats["ERA"] = round(totals["ER"] * 27 / totals["OUTS"], 3)
        stats["WHIP"] = round((totals["P_H"] + totals["P_BB"]) * 3 / totals["OUTS"], 4)
        stats["K/9"] = round(stats.get("K", 0) * 27 / totals["OUTS"], 2)
    if totals["P_BB"] > 0:
        stats["K/BB"] = round(stats.get("K", 0) / totals["P_BB"], 2)


# ──────────────────────────────────────────────
# API: My Roster (requires ESPN)
# ──────────────────────────────────────────────

@app.get("/api/my-roster")
async def api_my_roster(team_name: str = Query(None)):
    espn = get_espn()
    if not espn:
        return JSONResponse(status_code=400, content={"error": "ESPN league not configured. Add ESPN_S2 and ESPN_SWID to enable this feature."})

    import math

    categories = espn.get_stat_categories()
    cat_names = [c["name"] for c in categories]
    inverse_cats = {c["name"] for c in categories if c.get("is_inverse")}
    off_cats = [c["name"] for c in categories if c["type"] == "offense"]
    pit_cats = [c["name"] for c in categories if c["type"] == "pitching"]

    # Detect the user's own team via SWID
    swid = espn.espn_swid.strip('{}')
    my_team_name = None
    teams_list = []
    for t in espn.league.teams:
        owners = getattr(t, 'owners', [])
        for o in owners:
            oid = o.get('id', str(o)) if isinstance(o, dict) else str(o)
            if swid in oid:
                my_team_name = t.team_name
                break
        teams_list.append({"id": t.team_id, "name": t.team_name})

    # Auto-select user's team if no team specified
    if team_name is None:
        if my_team_name:
            team_name = my_team_name
        else:
            return {
                "teams": teams_list,
                "my_team": my_team_name,
                "needs_selection": True,
            }

    team_data = espn.get_my_team(team_name)
    players = team_data["players"]
    all_proj = get_projections()

    # Collect all league player stats for percentiles and z-scores
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

    # Mean/stddev for z-scores (VBR)
    stat_dist = {}
    for sname, vals in stat_values.items():
        n = len(vals)
        if n > 1:
            mean = sum(vals) / n
            variance = sum((v - mean) ** 2 for v in vals) / n
            std = math.sqrt(variance) if variance > 0 else 1.0
            stat_dist[sname] = {"mean": mean, "std": std}

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

        # VBR: sum of z-scores across relevant league categories
        is_pitcher = p.position in ('SP', 'RP', 'P') or p.lineup_slot == 'P'
        relevant_cats = pit_cats if is_pitcher else off_cats
        vbr = 0.0
        for cat in relevant_cats:
            val = p.stats.get(cat)
            if val is not None and isinstance(val, (int, float)) and cat in stat_dist:
                z = (val - stat_dist[cat]["mean"]) / stat_dist[cat]["std"]
                if cat in inverse_cats:
                    z = -z
                vbr += z

        player_list.append({
            "name": p.name, "position": p.position, "team": p.pro_team,
            "lineup_slot": p.lineup_slot,
            "eligible_slots": p.eligible_slots, "injury_status": p.injury_status,
            "pct_owned": p.percent_owned,
            "current_stats": p.stats, "projected_stats": p.projected_stats,
            "projections": projections,
            "grades": grades,
            "vbr": round(vbr, 1),
        })

    return {
        "team_name": team_data["team_name"],
        "players": player_list,
        "categories": categories,
        "teams": teams_list,
        "my_team": my_team_name,
        "needs_selection": False,
    }


# ──────────────────────────────────────────────
# API: Free Agents (requires ESPN)
# ──────────────────────────────────────────────

@app.get("/api/free-agents")
async def api_free_agents(
    position: str = Query(None),
    player_type: str = Query(None, enum=["batter", "pitcher"]),
    limit: int = Query(100, ge=10, le=300),
):
    espn = get_espn()
    if not espn:
        return JSONResponse(status_code=400, content={"error": "ESPN league not configured. Add ESPN_S2 and ESPN_SWID to enable this feature."})

    import math

    categories = espn.get_stat_categories()
    cat_names = [c["name"] for c in categories]
    inverse_cats = {c["name"] for c in categories if c.get("is_inverse")}
    off_cats = [c["name"] for c in categories if c["type"] == "offense"]
    pit_cats = [c["name"] for c in categories if c["type"] == "pitching"]

    # Fetch free agents — filter by position if specific, otherwise get all
    fa_position = position
    if not fa_position and player_type == "pitcher":
        fa_position = "SP"  # Will also get RP from a second call
    agents = espn.get_free_agents(size=limit, position=fa_position)

    # If pitcher type requested, also fetch RP
    if not position and player_type == "pitcher":
        rp_agents = espn.get_free_agents(size=50, position="RP")
        seen = {p.name for p in agents}
        for p in rp_agents:
            if p.name not in seen:
                agents.append(p)

    # Filter by player_type if set
    if player_type == "batter":
        agents = [p for p in agents if p.position not in ('SP', 'RP', 'P')]
    elif player_type == "pitcher":
        agents = [p for p in agents if p.position in ('SP', 'RP', 'P')]

    all_proj = get_projections()

    # Collect league-wide stats for z-score calculation
    all_players = []
    for team in espn.league.teams:
        for p in team.roster:
            all_players.append(espn._extract_player_info(p))

    stat_values = {}
    for p in all_players:
        for stat_name, val in p.stats.items():
            if stat_name in cat_names and isinstance(val, (int, float)):
                if stat_name not in stat_values:
                    stat_values[stat_name] = []
                stat_values[stat_name].append(val)

    stat_dist = {}
    for sname, vals in stat_values.items():
        n = len(vals)
        if n > 1:
            mean = sum(vals) / n
            variance = sum((v - mean) ** 2 for v in vals) / n
            std = math.sqrt(variance) if variance > 0 else 1.0
            stat_dist[sname] = {"mean": mean, "std": std}

    # Get user's roster for comparison
    swid = espn.espn_swid.strip('{}')
    my_roster_vbr = {}  # position -> worst VBR on my roster
    for team in espn.league.teams:
        owners = getattr(team, 'owners', [])
        is_mine = any(swid in (o.get('id', str(o)) if isinstance(o, dict) else str(o)) for o in owners)
        if not is_mine:
            continue
        for p in team.roster:
            info = espn._extract_player_info(p)
            if info.lineup_slot in ('IL', 'BE'):
                continue
            is_pit = info.position in ('SP', 'RP', 'P')
            rcats = pit_cats if is_pit else off_cats
            vbr = 0.0
            for cat in rcats:
                val = info.stats.get(cat)
                if val is not None and isinstance(val, (int, float)) and cat in stat_dist:
                    z = (val - stat_dist[cat]["mean"]) / stat_dist[cat]["std"]
                    if cat in inverse_cats:
                        z = -z
                    vbr += z
            # Track worst VBR per position for upgrade detection
            pos = info.position
            if pos not in my_roster_vbr or vbr < my_roster_vbr[pos]:
                my_roster_vbr[pos] = round(vbr, 1)
            # Also track by eligible slots
            for slot in info.eligible_slots:
                if slot not in ('BE', 'IL', 'UTIL'):
                    if slot not in my_roster_vbr or vbr < my_roster_vbr[slot]:
                        my_roster_vbr[slot] = round(vbr, 1)
        break

    player_list = []
    for p in agents:
        projections = get_player_projections(p.name, all_proj)
        if p.projected_stats:
            projections["ESPN"] = p.projected_stats

        is_pit = p.position in ('SP', 'RP', 'P')
        rcats = pit_cats if is_pit else off_cats

        # Current VBR
        cur_vbr = 0.0
        for cat in rcats:
            val = p.stats.get(cat)
            if val is not None and isinstance(val, (int, float)) and cat in stat_dist:
                z = (val - stat_dist[cat]["mean"]) / stat_dist[cat]["std"]
                if cat in inverse_cats:
                    z = -z
                cur_vbr += z

        # Projected VBR
        proj_vbr = 0.0
        proj_stats = p.projected_stats or {}
        for cat in rcats:
            val = proj_stats.get(cat)
            if val is not None and isinstance(val, (int, float)) and cat in stat_dist:
                z = (val - stat_dist[cat]["mean"]) / stat_dist[cat]["std"]
                if cat in inverse_cats:
                    z = -z
                proj_vbr += z

        # Check if this FA is an upgrade over user's worst at that position
        is_upgrade = False
        my_worst = my_roster_vbr.get(p.position)
        if my_worst is not None and cur_vbr > my_worst:
            is_upgrade = True

        player_list.append({
            "name": p.name, "position": p.position, "team": p.pro_team,
            "eligible_slots": p.eligible_slots, "injury_status": p.injury_status,
            "pct_owned": p.percent_owned, "pct_started": p.percent_started,
            "current_stats": p.stats, "projected_stats": p.projected_stats,
            "projections": projections,
            "vbr": round(cur_vbr, 1),
            "proj_vbr": round(proj_vbr, 1),
            "is_upgrade": is_upgrade,
        })

    # Sort by current VBR descending
    player_list.sort(key=lambda x: x["vbr"], reverse=True)

    return {
        "players": player_list,
        "categories": categories,
        "position": position,
        "player_type": player_type,
        "my_roster_vbr": my_roster_vbr,
    }


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

    # Check for current user's team
    swid = espn.espn_swid.strip('{}')
    teams_info = []
    for t in espn.league.teams:
        owners = getattr(t, 'owners', [])
        owner_ids = []
        for o in owners:
            if isinstance(o, dict):
                owner_ids.append(o.get('id', str(o)))
            else:
                owner_ids.append(str(o))
        teams_info.append({
            "name": t.team_name,
            "owners": owner_ids,
            "is_mine": any(swid in oid for oid in owner_ids),
        })

    return {"swid": swid, "teams": teams_info}
    cats = espn.get_stat_categories()
    cat_info = [{"name": c["name"], "display": c.get("display_name"), "type": c.get("type")} for c in cats]

    # Inspect league settings object fully
    settings = espn.league.settings
    settings_attrs = [a for a in dir(settings) if not a.startswith('_')]
    settings_values = {}
    for a in settings_attrs:
        val = getattr(settings, a, None)
        if not callable(val):
            settings_values[a] = str(val)[:200]

    # Check both period keys for first player
    player_periods = {}
    if team.roster:
        p = team.roster[0]
        for period_key, period_data in (p.stats or {}).items():
            bd = period_data.get("breakdown", {})
            player_periods[str(period_key)] = {
                "keys_in_period": list(period_data.keys()),
                "breakdown_all_keys": list(bd.keys()),
                "breakdown_sample": dict(list(bd.items())[:15]),
                "points": period_data.get("points"),
                "projected_points": period_data.get("projected_points"),
            }

    # Check a hitter too
    hitter_periods = {}
    for p in team.roster:
        pos = getattr(p, 'position', '')
        if pos not in ('SP', 'RP', 'P') and isinstance(pos, str):
            for period_key, period_data in (p.stats or {}).items():
                bd = period_data.get("breakdown", {})
                hitter_periods[str(period_key)] = {
                    "player": p.name,
                    "breakdown_all_keys": list(bd.keys()),
                    "breakdown_sample": dict(list(bd.items())[:15]),
                }
            break

    # Roster slot info for each player
    roster_slots = []
    for p in team.roster:
        attrs = [a for a in dir(p) if not a.startswith('_')]
        slot_info = {
            "name": p.name,
            "position": getattr(p, 'position', None),
            "lineupSlot": getattr(p, 'lineupSlot', None),
            "eligibleSlots": getattr(p, 'eligibleSlots', None),
            "injuryStatus": getattr(p, 'injuryStatus', None),
            "attrs": attrs,
        }
        roster_slots.append(slot_info)

    # League roster settings
    roster_settings = getattr(settings, 'roster', None)
    roster_settings_str = str(roster_settings)[:500] if roster_settings else "NOT_FOUND"
    roster_slots_setting = getattr(settings, 'roster_slots', None)
    roster_slots_str = str(roster_slots_setting)[:500] if roster_slots_setting else "NOT_FOUND"

    return {
        "team_name": team.team_name,
        "wins": team.wins, "losses": team.losses, "ties": getattr(team, 'ties', 0),
        "league_categories": cat_info,
        "settings_attrs": settings_values,
        "roster_slots": roster_slots,
        "roster_setting": roster_settings_str,
        "roster_slots_setting": roster_slots_str,
        "pitcher_periods": player_periods,
        "hitter_periods": hitter_periods,
    }
