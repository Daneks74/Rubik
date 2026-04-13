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

# Standard H2H category sets — used to auto-detect league categories
OFFENSE_STATS = {"AB", "H", "AVG", "2B", "3B", "HR", "XBH", "1B", "TB",
                 "SLG", "B_BB", "HBP", "PA", "R", "RBI", "SB", "CS",
                 "OBP", "OPS", "B_SO", "GDP", "SF", "SH", "RC"}
PITCHING_STATS = {"OUTS", "P_H", "ER", "K", "P_BB", "P_HR", "GS", "W", "L",
                  "SV", "ERA", "WHIP", "HLD", "BLSV", "QS", "K/9", "K/BB",
                  "SV+HLD", "GP", "CG", "SVO"}

# Common H2H scoring categories (keys as they appear in ESPN breakdowns)
# These are the stats that typically count in roto/H2H leagues
COMMON_OFFENSE_CATS = ["AVG", "R", "RBI", "HR", "SB", "OBP", "OPS", "SLG", "TB", "B_BB", "H", "B_SO"]
COMMON_PITCHING_CATS = ["ERA", "WHIP", "K", "W", "SV", "QS", "HLD", "K/9", "SV+HLD", "OUTS", "K/BB"]

# Stats where lower is better
INVERSE_STATS = {"ERA", "WHIP", "L", "ER", "P_BB", "P_HR", "BLSV", "B_SO"}

# Display-friendly names for breakdown keys
DISPLAY_NAMES = {
    "AVG": "AVG", "R": "R", "RBI": "RBI", "HR": "HR", "SB": "SB",
    "OBP": "OBP", "OPS": "OPS", "SLG": "SLG", "H": "H", "B_BB": "BB",
    "B_SO": "SO", "TB": "TB", "2B": "2B", "3B": "3B", "XBH": "XBH",
    "CS": "CS", "GDP": "GDP", "AB": "AB", "PA": "PA", "SF": "SF",
    "ERA": "ERA", "WHIP": "WHIP", "K": "K", "W": "W", "SV": "SV",
    "QS": "QS", "HLD": "HLD", "K/9": "K/9", "K/BB": "K/BB",
    "OUTS": "IP", "L": "L", "ER": "ER", "P_H": "H(P)", "P_BB": "BB(P)",
    "P_HR": "HR(P)", "GS": "GS", "GP": "GP", "CG": "CG",
    "SVO": "SVO", "BLSV": "BS", "SV+HLD": "SV+H",
}


class ESPNClient:
    def __init__(self, league_id: int, espn_s2: str, espn_swid: str, season_year: int):
        self.league_id = league_id
        self.espn_s2 = espn_s2
        self.espn_swid = espn_swid
        self.season_year = season_year
        self._league = None
        self._detected_categories = None

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

    def _detect_categories(self) -> list[dict]:
        """Auto-detect league scoring categories from player breakdown data."""
        if self._detected_categories is not None:
            return self._detected_categories

        # Collect all breakdown keys from a sample of players
        offense_keys = set()
        pitching_keys = set()
        for team in self.league.teams:
            for player in team.roster[:5]:
                if not hasattr(player, 'stats') or not player.stats:
                    continue
                # Period 0 = season totals
                period = player.stats.get(0, {})
                bd = period.get("breakdown", {})
                pos = getattr(player, 'position', '')
                if pos in ('SP', 'RP', 'P'):
                    pitching_keys.update(bd.keys())
                else:
                    offense_keys.update(bd.keys())
            if offense_keys and pitching_keys:
                break

        # Match against known common categories
        categories = []
        for key in COMMON_OFFENSE_CATS:
            if key in offense_keys:
                categories.append({
                    "name": key,
                    "display_name": DISPLAY_NAMES.get(key, key),
                    "type": "offense",
                    "is_inverse": key in INVERSE_STATS,
                })
        for key in COMMON_PITCHING_CATS:
            if key in pitching_keys:
                categories.append({
                    "name": key,
                    "display_name": DISPLAY_NAMES.get(key, key),
                    "type": "pitching",
                    "is_inverse": key in INVERSE_STATS,
                })

        self._detected_categories = categories
        return categories

    def get_league_settings(self) -> dict:
        """Get league name and scoring categories."""
        settings = self.league.settings
        scoring_type = getattr(settings, 'scoring_type', 'Unknown')
        categories = self._detect_categories()

        return {
            "league_name": getattr(settings, 'name', 'Unknown League'),
            "scoring_type": scoring_type,
            "categories": categories,
            "num_teams": len(self.league.teams),
        }

    def get_stat_categories(self) -> list[dict]:
        """Get the league's scoring categories split into offense/pitching."""
        return self._detect_categories()

    def _get_player_breakdown(self, player, period_key=0) -> dict:
        """Get a player's stat breakdown for a period. Period 0 = season totals."""
        if not hasattr(player, 'stats') or not player.stats:
            return {}
        period = player.stats.get(period_key, {})
        return period.get("breakdown", {})

    def _get_player_projected_breakdown(self, player) -> dict:
        """Get a player's projected stat breakdown (season totals only)."""
        if not hasattr(player, 'stats') or not player.stats:
            return {}
        period = player.stats.get(0, {})
        return period.get("projected_breakdown", {})

    def get_teams_with_stats(self) -> list[dict]:
        """Get all teams with their current stats by category."""
        teams_data = []

        for team in self.league.teams:
            team_stats = {}
            for player in team.roster:
                bd = self._get_player_breakdown(player, period_key=0)
                for key, value in bd.items():
                    if key in team_stats:
                        team_stats[key] = team_stats[key] + value
                    else:
                        team_stats[key] = value

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
        # Get eligible positions (already strings in current espn-api)
        eligible = []
        if hasattr(player, 'eligibleSlots'):
            for slot in player.eligibleSlots:
                if isinstance(slot, int):
                    slot = POSITION_MAP.get(slot, '')
                if slot and slot not in ("IL", "BE"):
                    eligible.append(slot)

        # Get primary position
        position = getattr(player, 'position', 'Unknown')
        if isinstance(position, int):
            position = POSITION_MAP.get(position, 'Unknown')

        # Get lineup slot (the actual roster position: C, 1B, IF, BE, IL, P, etc.)
        lineup_slot = getattr(player, 'lineupSlot', '')
        if isinstance(lineup_slot, int):
            lineup_slot = POSITION_MAP.get(lineup_slot, '')

        # Get current and projected stats directly from breakdowns (string keys)
        current_stats = self._get_player_breakdown(player, period_key=0)
        projected_stats = self._get_player_projected_breakdown(player)

        total_pts = 0.0
        proj_pts = 0.0
        if hasattr(player, 'stats') and player.stats:
            for period_id, period_data in player.stats.items():
                total_pts += period_data.get("points", 0) or 0
                proj_pts += period_data.get("projected_points", 0) or 0

        pro_team = getattr(player, 'proTeam', 'FA')
        if isinstance(pro_team, int):
            pro_team = PRO_TEAM_MAP.get(pro_team, 'FA')

        return PlayerInfo(
            espn_id=player.playerId if hasattr(player, 'playerId') else 0,
            name=player.name,
            pro_team=pro_team,
            position=position,
            eligible_slots=eligible,
            lineup_slot=lineup_slot,
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
