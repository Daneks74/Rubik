"""
Lightweight team-context layer — provides team-level heuristics for the
projection engine without requiring full lineup ingestion.

Each team has five factors centered on 1.0 (neutral):
  - offense_strength:      stronger offense → harder on opposing pitchers
  - offense_k_tendency:    higher → lineup strikes out more (easier to K)
  - win_support_factor:    higher → better run support for own pitchers
  - bullpen_support_factor: higher → better chance of holding leads
  - run_environment_factor: higher → more run-scoring environment (park + context)

Defaults are static estimates based on recent MLB team profiles.
Designed for easy replacement with real ingested team metrics later.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import TeamContext

logger = logging.getLogger(__name__)


# ── Default team context for all 30 MLB teams ──
# Values centered on 1.0 (neutral). Differentiated by rough 2024-2025 team
# profiles. Keys use the same normalized team codes as the rest of the pipeline.

_DEFAULT_TEAMS: dict[str, dict[str, float]] = {
    # ── AL East ──
    "NYY": {"offense_strength": 1.07, "offense_k_tendency": 0.96, "win_support_factor": 1.06, "bullpen_support_factor": 1.03, "run_environment_factor": 1.00},
    "Bal": {"offense_strength": 1.04, "offense_k_tendency": 0.99, "win_support_factor": 1.03, "bullpen_support_factor": 1.02, "run_environment_factor": 0.99},
    "Bos": {"offense_strength": 1.01, "offense_k_tendency": 0.98, "win_support_factor": 1.00, "bullpen_support_factor": 0.99, "run_environment_factor": 1.03},
    "TB":  {"offense_strength": 0.98, "offense_k_tendency": 1.01, "win_support_factor": 0.99, "bullpen_support_factor": 1.02, "run_environment_factor": 0.97},
    "Tor": {"offense_strength": 0.99, "offense_k_tendency": 1.00, "win_support_factor": 0.98, "bullpen_support_factor": 0.98, "run_environment_factor": 1.00},
    # ── AL Central ──
    "Cle": {"offense_strength": 1.02, "offense_k_tendency": 0.97, "win_support_factor": 1.03, "bullpen_support_factor": 1.06, "run_environment_factor": 0.96},
    "Min": {"offense_strength": 1.03, "offense_k_tendency": 1.00, "win_support_factor": 1.02, "bullpen_support_factor": 0.99, "run_environment_factor": 1.00},
    "Det": {"offense_strength": 0.94, "offense_k_tendency": 1.06, "win_support_factor": 0.95, "bullpen_support_factor": 0.96, "run_environment_factor": 0.97},
    "KC":  {"offense_strength": 0.98, "offense_k_tendency": 1.02, "win_support_factor": 0.98, "bullpen_support_factor": 0.99, "run_environment_factor": 1.00},
    "ChW": {"offense_strength": 0.90, "offense_k_tendency": 1.08, "win_support_factor": 0.91, "bullpen_support_factor": 0.92, "run_environment_factor": 0.96},
    # ── AL West ──
    "Hou": {"offense_strength": 1.06, "offense_k_tendency": 0.96, "win_support_factor": 1.05, "bullpen_support_factor": 1.04, "run_environment_factor": 1.00},
    "Sea": {"offense_strength": 1.00, "offense_k_tendency": 1.02, "win_support_factor": 1.01, "bullpen_support_factor": 1.02, "run_environment_factor": 0.97},
    "Tex": {"offense_strength": 1.03, "offense_k_tendency": 0.99, "win_support_factor": 1.02, "bullpen_support_factor": 0.99, "run_environment_factor": 1.05},
    "LAA": {"offense_strength": 0.93, "offense_k_tendency": 1.04, "win_support_factor": 0.93, "bullpen_support_factor": 0.93, "run_environment_factor": 0.98},
    "Oak": {"offense_strength": 0.91, "offense_k_tendency": 1.07, "win_support_factor": 0.91, "bullpen_support_factor": 0.91, "run_environment_factor": 0.96},
    # ── NL East ──
    "Atl": {"offense_strength": 1.06, "offense_k_tendency": 0.96, "win_support_factor": 1.06, "bullpen_support_factor": 1.05, "run_environment_factor": 1.02},
    "Phi": {"offense_strength": 1.05, "offense_k_tendency": 0.97, "win_support_factor": 1.05, "bullpen_support_factor": 1.04, "run_environment_factor": 1.04},
    "NYM": {"offense_strength": 1.00, "offense_k_tendency": 0.97, "win_support_factor": 0.99, "bullpen_support_factor": 0.99, "run_environment_factor": 0.96},
    "Wsh": {"offense_strength": 0.93, "offense_k_tendency": 1.03, "win_support_factor": 0.94, "bullpen_support_factor": 0.95, "run_environment_factor": 0.97},
    "Mia": {"offense_strength": 0.92, "offense_k_tendency": 1.05, "win_support_factor": 0.93, "bullpen_support_factor": 0.96, "run_environment_factor": 0.96},
    # ── NL Central ──
    "Mil": {"offense_strength": 1.01, "offense_k_tendency": 0.98, "win_support_factor": 1.01, "bullpen_support_factor": 1.04, "run_environment_factor": 1.01},
    "ChC": {"offense_strength": 1.00, "offense_k_tendency": 1.00, "win_support_factor": 1.00, "bullpen_support_factor": 0.99, "run_environment_factor": 1.02},
    "StL": {"offense_strength": 0.97, "offense_k_tendency": 1.00, "win_support_factor": 0.97, "bullpen_support_factor": 0.97, "run_environment_factor": 0.97},
    "Pit": {"offense_strength": 0.95, "offense_k_tendency": 1.03, "win_support_factor": 0.95, "bullpen_support_factor": 0.96, "run_environment_factor": 0.96},
    "Cin": {"offense_strength": 1.00, "offense_k_tendency": 1.01, "win_support_factor": 0.99, "bullpen_support_factor": 0.97, "run_environment_factor": 1.06},
    # ── NL West ──
    "LAD": {"offense_strength": 1.08, "offense_k_tendency": 0.95, "win_support_factor": 1.07, "bullpen_support_factor": 1.05, "run_environment_factor": 1.00},
    "SD":  {"offense_strength": 1.03, "offense_k_tendency": 0.98, "win_support_factor": 1.02, "bullpen_support_factor": 1.03, "run_environment_factor": 0.98},
    "Ari": {"offense_strength": 0.97, "offense_k_tendency": 1.02, "win_support_factor": 0.97, "bullpen_support_factor": 0.96, "run_environment_factor": 1.03},
    "SF":  {"offense_strength": 0.99, "offense_k_tendency": 0.99, "win_support_factor": 0.99, "bullpen_support_factor": 1.01, "run_environment_factor": 0.98},
    "Col": {"offense_strength": 0.96, "offense_k_tendency": 1.05, "win_support_factor": 0.95, "bullpen_support_factor": 0.94, "run_environment_factor": 1.10},
}

# Neutral context returned when a team code is not found
NEUTRAL_TEAM_CTX: dict[str, float] = {
    "offense_strength": 1.0,
    "offense_k_tendency": 1.0,
    "win_support_factor": 1.0,
    "bullpen_support_factor": 1.0,
    "run_environment_factor": 1.0,
}


def get_default_team_context(season_year: int) -> list[dict]:
    """Return a full set of default team context records for all 30 MLB teams.

    Returns a list of dicts ready for upsert_team_context().
    """
    rows = []
    for team_code, factors in _DEFAULT_TEAMS.items():
        rows.append({
            "team_code": team_code,
            "season_year": season_year,
            **factors,
        })
    logger.info("Generated %d default team context records for %d", len(rows), season_year)
    return rows


def upsert_team_context(session: Session, context_rows: list[dict]) -> int:
    """Insert or update team context rows by (team_code, season_year).

    Does NOT commit — caller is responsible for committing.
    Returns the number of rows upserted.
    """
    now = datetime.now(timezone.utc)
    count = 0

    for row in context_rows:
        existing = (
            session.query(TeamContext)
            .filter(
                TeamContext.team_code == row["team_code"],
                TeamContext.season_year == row["season_year"],
            )
            .first()
        )

        if existing:
            existing.offense_strength = row.get("offense_strength", existing.offense_strength)
            existing.offense_k_tendency = row.get("offense_k_tendency", existing.offense_k_tendency)
            existing.win_support_factor = row.get("win_support_factor", existing.win_support_factor)
            existing.bullpen_support_factor = row.get("bullpen_support_factor", existing.bullpen_support_factor)
            existing.run_environment_factor = row.get("run_environment_factor", existing.run_environment_factor)
            existing.updated_at = now
        else:
            session.add(TeamContext(
                team_code=row["team_code"],
                season_year=row["season_year"],
                offense_strength=row.get("offense_strength"),
                offense_k_tendency=row.get("offense_k_tendency"),
                win_support_factor=row.get("win_support_factor"),
                bullpen_support_factor=row.get("bullpen_support_factor"),
                run_environment_factor=row.get("run_environment_factor"),
            ))
        count += 1

    session.flush()
    return count


def get_team_context_map(session: Session, season_year: int) -> dict[str, dict]:
    """Load team context for a season, keyed by team_code.

    If no rows exist for the season, auto-seeds defaults and persists them.
    Returns a dict like {"NYY": {"offense_strength": 1.07, ...}, ...}.
    """
    rows = (
        session.query(TeamContext)
        .filter(TeamContext.season_year == season_year)
        .all()
    )

    if not rows:
        logger.info("No team context for %d — auto-seeding defaults", season_year)
        defaults = get_default_team_context(season_year)
        upsert_team_context(session, defaults)
        session.commit()
        rows = (
            session.query(TeamContext)
            .filter(TeamContext.season_year == season_year)
            .all()
        )

    logger.info("Loaded team context: %d teams for %d", len(rows), season_year)

    return {
        r.team_code: {
            "offense_strength": r.offense_strength,
            "offense_k_tendency": r.offense_k_tendency,
            "win_support_factor": r.win_support_factor,
            "bullpen_support_factor": r.bullpen_support_factor,
            "run_environment_factor": r.run_environment_factor,
        }
        for r in rows
    }
