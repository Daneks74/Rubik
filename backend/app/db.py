import logging
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session, declarative_base, sessionmaker

load_dotenv()
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db():
    """Create all tables defined in models."""
    from app import models  # noqa: F401 — ensure models are registered

    logger.info("Connecting to database...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created successfully")


def prune_old_data(session: Session) -> dict[str, int]:
    """Delete stale rows to keep storage small. Returns counts deleted per table."""
    from app.models import (
        AppRun, DailyPitcherProjection, LineupAggregate, PitcherBaseline,
        ProbableStarter, ProjectedLineup, TeamContext,
    )

    now = datetime.now(timezone.utc)
    current_year = now.year
    counts = {}

    # probable_starters older than 7 days
    cutoff = now - timedelta(days=7)
    result = session.execute(
        delete(ProbableStarter).where(ProbableStarter.created_at < cutoff)
    )
    counts["probable_starters"] = result.rowcount

    # daily_pitcher_projections older than 14 days
    cutoff = now - timedelta(days=14)
    result = session.execute(
        delete(DailyPitcherProjection).where(DailyPitcherProjection.generated_at < cutoff)
    )
    counts["daily_pitcher_projections"] = result.rowcount

    # pitcher_baselines: remove non-current-season OR updated more than 30 days ago
    cutoff = now - timedelta(days=30)
    result = session.execute(
        delete(PitcherBaseline).where(
            (PitcherBaseline.season_year != current_year) |
            (PitcherBaseline.updated_at < cutoff)
        )
    )
    counts["pitcher_baselines"] = result.rowcount

    # team_context: keep current + previous season, delete older
    result = session.execute(
        delete(TeamContext).where(TeamContext.season_year < current_year - 1)
    )
    counts["team_context"] = result.rowcount

    # projected_lineups older than 7 days
    cutoff = now - timedelta(days=7)
    result = session.execute(
        delete(ProjectedLineup).where(ProjectedLineup.created_at < cutoff)
    )
    counts["projected_lineups"] = result.rowcount

    # lineup_aggregates older than 7 days
    result = session.execute(
        delete(LineupAggregate).where(LineupAggregate.created_at < cutoff)
    )
    counts["lineup_aggregates"] = result.rowcount

    # app_runs older than 30 days
    cutoff = now - timedelta(days=30)
    result = session.execute(
        delete(AppRun).where(AppRun.created_at < cutoff)
    )
    counts["app_runs"] = result.rowcount

    session.commit()
    return counts
