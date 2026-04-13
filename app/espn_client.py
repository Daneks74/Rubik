import logging
from espn_api.baseball import League
from app.config import AppConfig
from app.models import PlayerInfo

logger = logging.getLogger(__name__)

# ESPN team abbreviation mapping (proTeam values)
PRO_TEAM_MAP = {
    0: "FA", 1: "Ari", 2: "Atl", 3: "Bal", 4: "Bos", 5: "ChC",
    6: "ChW", 7: "Cin", 8: "Cle", 9: "Col", 10: "Det", 11: "Fla",
    12: "Hou", 13: "KC", 14: "LAA", 15: "LAD", 16: "Mil", 17: "Min",
    18: "NYM", 19: "NYY", 20: "Oak", 21: "Phi", 22: "Pit", 23: "SD",
    24: "SF", 25: "Sea", 26: "StL", 27: "TB", 28: "Tex", 29: "Tor",
    30: "Wsh",
}

POSITION_MAP = {
    0: "C", 1: "1B", 2: "2B", 3: "3B", 4: "SS", 5: "OF",
    6: "UTIL", 7: "DH", 8: "SP", 9: "RP", 10: "P", 19: "IL",
}

STAT_ID_MAP = {
    0: "AB", 1: "H", 2: "AVG", 3: "2B", 4: "3B", 5: "HR",
    6: "XBH", 7: "1B", 8: "TB", 10: "SLUG", 11: "BB", 12: "IBB",
    13: "HBP", 14: "SF", 15: "SH", 16: "SAC", 17: "PA",
    20: "R", 21: "RBI", 22: "ROE", 23: "FC", 24: "E",
    25: "SB", 26: "CS", 27: "GIDP", 28: "GDP",
    29: "OBP", 30: "OPS",
    33: "SO", 34: "K/BB",
    37: "FPCT",
    40: "IP", 41: "H_P", 42: "ER", 43: "K_P", 44: "BB_P",
    45: "HR_P", 46: "GS", 47: "W", 48: "L", 49: "SV",
    50: "ERA", 51: "WHIP", 53: "HLD", 54: "BS",
    57: "QS", 58: "GP",
    60: "K/9", 61: "BB/9", 62: "K/BB_P",
    63: "SV+HLD", 65: "APP",
    99: "TOTAL",
}


class ESPNClient:
    def __init__(self, league_id: int, espn_s2: str, espn_swid: str, season_year: int):
        self.league_id = league_id
        self.espn_s2 = espn_s2
        self.espn_swid = espn_swid
        self.season_year = season_year
        self._league = None

    @property
    def league(self) -> League:
        if self._league is None:
            self._league = League(
                league_id=self.league_id,
                year=self.season_year,
                espn_s2=self.espn_s2,
                swid=self.espn_swid,
            )
        return self._league

    @staticmethod
    def connect(league_id: int, config: AppConfig) -> "ESPNClient":
        """Create an ESPNClient for a specific league."""
        return ESPNClient(league_id, config.espn_s2, config.espn_swid, config.season_year)

    def get_league_name(self) -> str:
        """Quick call to get just the league name."""
        try:
            return getattr(self.league.settings, 'name', f'League {self.league_id}')
        except Exception:
            return f'League {self.league_id}'

    def get_league_settings(self) -> dict:
        """Get league name and scoring categories."""
        settings = self.league.settings
        scoring_type = getattr(settings, 'scoring_type', 'Unknown')

        # Extract stat categories from the league's stat_categories
        categories = []
        stat_cats = getattr(settings, 'stat_categories', [])
        for cat in stat_cats:
            cat_id = getattr(cat, 'id', None)
            cat_name = STAT_ID_MAP.get(cat_id, f"STAT_{cat_id}") if cat_id is not None else "Unknown"
            categories.append({
                "id": cat_id,
                "name": cat_name,
                "display_name": getattr(cat, 'display_name', cat_name),
                "is_inverse": cat_name in ("ERA", "WHIP", "L", "ER", "BB_P", "HR_P", "BS"),
            })

        return {
            "league_name": getattr(settings, 'name', 'Unknown League'),
            "scoring_type": scoring_type,
            "categories": categories,
            "num_teams": len(self.league.teams),
        }

    def get_stat_categories(self) -> list[dict]:
        """Get the league's scoring categories split into offense/defense."""
        settings = self.get_league_settings()
        categories = settings.get("categories", [])

        offense_stats = {"AB", "H", "AVG", "2B", "3B", "HR", "XBH", "1B", "TB",
                         "SLUG", "BB", "HBP", "PA", "R", "RBI", "SB", "CS",
                         "OBP", "OPS", "SO", "GIDP"}
        pitching_stats = {"IP", "H_P", "ER", "K_P", "BB_P", "HR_P", "GS", "W", "L",
                          "SV", "ERA", "WHIP", "HLD", "BS", "QS", "K/9", "BB/9",
                          "K/BB_P", "SV+HLD", "APP"}

        for cat in categories:
            name = cat["name"]
            if name in offense_stats:
                cat["type"] = "offense"
            elif name in pitching_stats:
                cat["type"] = "pitching"
            else:
                cat["type"] = "other"

        return categories

    def get_teams_with_stats(self) -> list[dict]:
        """Get all teams with their current stats by category."""
        categories = self.get_stat_categories()
        teams_data = []

        for team in self.league.teams:
            team_stats = {}
            # Aggregate stats from roster
            for player in team.roster:
                if player.stats:
                    for period_id, period_stats in player.stats.items():
                        # Use total stats (period '002026' or similar for season)
                        breakdown = period_stats.get("breakdown", {})
                        for stat_id_str, value in breakdown.items():
                            stat_id = int(stat_id_str) if isinstance(stat_id_str, str) else stat_id_str
                            stat_name = STAT_ID_MAP.get(stat_id, f"STAT_{stat_id}")
                            if stat_name not in team_stats:
                                team_stats[stat_name] = 0.0
                            team_stats[stat_name] += value

            teams_data.append({
                "team_id": team.team_id,
                "team_name": team.team_name,
                "team_abbrev": team.team_abbrev,
                "logo_url": getattr(team, 'logo_url', ''),
                "wins": team.wins,
                "losses": team.losses,
                "ties": getattr(team, 'ties', 0),
                "standing": team.standing,
                "stats": team_stats,
                "roster_count": len(team.roster),
            })

        return teams_data

    def get_standings(self) -> list[dict]:
        """Get league standings with wins/losses."""
        standings = self.league.standings()
        return [{
            "team_id": team.team_id,
            "team_name": team.team_name,
            "team_abbrev": team.team_abbrev,
            "wins": team.wins,
            "losses": team.losses,
            "ties": getattr(team, 'ties', 0),
            "standing": team.standing,
            "logo_url": getattr(team, 'logo_url', ''),
        } for team in standings]

    def _extract_player_info(self, player) -> PlayerInfo:
        """Convert ESPN player object to PlayerInfo."""
        # Get eligible positions
        eligible = []
        if hasattr(player, 'eligibleSlots'):
            for slot_id in player.eligibleSlots:
                pos = POSITION_MAP.get(slot_id)
                if pos and pos not in ("IL", "UTIL"):
                    eligible.append(pos)

        # Get primary position
        position = getattr(player, 'position', 'Unknown')
        if isinstance(position, int):
            position = POSITION_MAP.get(position, 'Unknown')

        # Get current and projected stats
        current_stats = {}
        projected_stats = {}
        total_pts = 0.0
        proj_pts = 0.0

        if hasattr(player, 'stats') and player.stats:
            for period_id, period_data in player.stats.items():
                breakdown = period_data.get("breakdown", {})
                pts = period_data.get("points", 0) or 0
                proj = period_data.get("projected_points", 0) or 0
                proj_breakdown = period_data.get("projected_breakdown", {})

                # Map stat IDs to names
                for stat_id_str, value in breakdown.items():
                    stat_id = int(stat_id_str) if isinstance(stat_id_str, str) else stat_id_str
                    stat_name = STAT_ID_MAP.get(stat_id, f"STAT_{stat_id}")
                    current_stats[stat_name] = value

                for stat_id_str, value in proj_breakdown.items():
                    stat_id = int(stat_id_str) if isinstance(stat_id_str, str) else stat_id_str
                    stat_name = STAT_ID_MAP.get(stat_id, f"STAT_{stat_id}")
                    projected_stats[stat_name] = value

                total_pts += pts
                proj_pts += proj

        pro_team = getattr(player, 'proTeam', 'FA')
        if isinstance(pro_team, int):
            pro_team = PRO_TEAM_MAP.get(pro_team, 'FA')

        return PlayerInfo(
            espn_id=player.playerId if hasattr(player, 'playerId') else 0,
            name=player.name,
            pro_team=pro_team,
            position=position,
            eligible_slots=eligible,
            percent_owned=round(getattr(player, 'percent_owned', 0) or 0, 1),
            percent_started=round(getattr(player, 'percent_started', 0) or 0, 1),
            injury_status=getattr(player, 'injuryStatus', 'ACTIVE') or 'ACTIVE',
            stats=current_stats,
            projected_stats=projected_stats,
            total_points=round(total_pts, 1),
            projected_points=round(proj_pts, 1),
        )

    def get_my_team(self, team_name: str = None) -> dict:
        """Get the user's team roster with stats. If team_name is None, returns first team."""
        for team in self.league.teams:
            if team_name is None or team.team_name.lower() == team_name.lower():
                players = [self._extract_player_info(p) for p in team.roster]
                return {
                    "team_name": team.team_name,
                    "team_id": team.team_id,
                    "players": players,
                }
        return {"team_name": "Unknown", "team_id": 0, "players": []}

    def get_all_rosters(self) -> dict[str, list[str]]:
        """Get a set of all rostered player names, keyed by team."""
        rosters = {}
        for team in self.league.teams:
            rosters[team.team_name] = [p.name for p in team.roster]
        return rosters

    def get_free_agents(self, size: int = 100, position: str = None) -> list[PlayerInfo]:
        """Get free agents, optionally filtered by position."""
        kwargs = {"size": size}
        if position:
            kwargs["position"] = position

        try:
            agents = self.league.free_agents(**kwargs)
        except Exception as e:
            logger.error("Failed to fetch free agents: %s", e)
            return []

        results = []
        for player in agents:
            info = self._extract_player_info(player)
            if info.injury_status not in ("IL", "IL60", "OUT", "SUSPENSION"):
                results.append(info)
        return results

    def get_free_agent_sps(self, size: int = 100) -> list[PlayerInfo]:
        """Get free agent starting pitchers."""
        return self.get_free_agents(size=size, position="SP")
