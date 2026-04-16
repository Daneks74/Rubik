from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, String, Text, UniqueConstraint

from app.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


class ProbableStarter(Base):
    __tablename__ = "probable_starters"

    id = Column(Integer, primary_key=True)
    game_date = Column(Date, nullable=False, index=True)
    game_id = Column(String, nullable=False, index=True)
    pitcher_name = Column(String, nullable=False, index=True)
    pitcher_team = Column(String, nullable=False, index=True)
    opponent_team = Column(String, nullable=False)
    home_away = Column(String, nullable=False)
    throws = Column(String, nullable=True)
    status = Column(String, nullable=False)
    source = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class PitcherBaseline(Base):
    __tablename__ = "pitcher_baselines"

    id = Column(Integer, primary_key=True)
    pitcher_name = Column(String, nullable=False, index=True)
    pitcher_team = Column(String, nullable=False, index=True)
    season_year = Column(Integer, nullable=False, index=True)
    ros_ip_per_start = Column(Float, nullable=True)
    ros_k_pct = Column(Float, nullable=True)
    ros_bb_pct = Column(Float, nullable=True)
    ros_era = Column(Float, nullable=True)
    ros_whip = Column(Float, nullable=True)
    xera = Column(Float, nullable=True)
    xwoba = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=_utcnow)


class DailyPitcherProjection(Base):
    __tablename__ = "daily_pitcher_projections"

    id = Column(Integer, primary_key=True)
    game_date = Column(Date, nullable=False, index=True)
    game_id = Column(String, nullable=False, index=True)
    pitcher_name = Column(String, nullable=False, index=True)
    pitcher_team = Column(String, nullable=False, index=True)
    opponent_team = Column(String, nullable=False)
    projected_ip = Column(Float, nullable=True)
    projected_k = Column(Float, nullable=True)
    projected_bb = Column(Float, nullable=True)
    projected_h = Column(Float, nullable=True)
    projected_er = Column(Float, nullable=True)
    projected_era = Column(Float, nullable=True)
    projected_whip = Column(Float, nullable=True)
    win_probability = Column(Float, nullable=True)
    blowup_probability = Column(Float, nullable=True)
    confidence = Column(String, nullable=True)
    stream_score = Column(Float, nullable=True)
    model_version = Column(String, nullable=True)
    generated_at = Column(DateTime, default=_utcnow)


class TeamContext(Base):
    __tablename__ = "team_context"
    __table_args__ = (
        UniqueConstraint("team_code", "season_year", name="uq_team_context_team_season"),
    )

    id = Column(Integer, primary_key=True)
    team_code = Column(String, nullable=False, index=True)
    season_year = Column(Integer, nullable=False, index=True)
    offense_strength = Column(Float, nullable=True)
    offense_k_tendency = Column(Float, nullable=True)
    win_support_factor = Column(Float, nullable=True)
    bullpen_support_factor = Column(Float, nullable=True)
    run_environment_factor = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=_utcnow)


class ProjectedLineup(Base):
    __tablename__ = "projected_lineups"

    id = Column(Integer, primary_key=True)
    game_date = Column(Date, nullable=False, index=True)
    game_id = Column(String, nullable=False, index=True)
    team_code = Column(String, nullable=False, index=True)
    batting_order = Column(Integer, nullable=False)
    hitter_name = Column(String, nullable=False)
    bats = Column(String, nullable=True)
    confirmed = Column(Boolean, nullable=False, default=False)
    source = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class LineupAggregate(Base):
    __tablename__ = "lineup_aggregates"
    __table_args__ = (
        UniqueConstraint("game_date", "game_id", "team_code", "vs_hand",
                         name="uq_lineup_agg_game_team_hand"),
    )

    id = Column(Integer, primary_key=True)
    game_date = Column(Date, nullable=False, index=True)
    game_id = Column(String, nullable=False, index=True)
    team_code = Column(String, nullable=False, index=True)
    vs_hand = Column(String, nullable=False)
    agg_k_tendency = Column(Float, nullable=True)
    agg_bb_tendency = Column(Float, nullable=True)
    agg_offense_strength = Column(Float, nullable=True)
    agg_contact_quality = Column(Float, nullable=True)
    hitter_count = Column(Integer, nullable=True)
    source = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class AppRun(Base):
    __tablename__ = "app_runs"

    id = Column(Integer, primary_key=True)
    run_type = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
