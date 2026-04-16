"""
Refresh orchestration — runs the data pipeline stages in order,
tracks freshness, and supports partial/forced refreshes.

Stages (in order):
1. probable_starters
2. team_context
3. baselines
4. lineups
5. projections
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.baselines import get_pitcher_baselines_for_starters, upsert_pitcher_baselines
from app.cache import cache
from app.lineups import (
    get_lineup_aggregate_map,
    refresh_projected_lineups_and_aggregates,
)
from app.models import (
    AppRun,
    DailyPitcherProjection,
    PitcherBaseline,
    ProbableStarter,
)
from app.probable_starters import get_probable_starters
from app.projection_builder import BaselineRecord, StarterRecord
from app.projection_engine import MODEL_VERSION, build_daily_projections_for_starters
from app.team_context import get_team_context_map, refresh_team_context

logger = logging.getLogger(__name__)

# ── Freshness thresholds (minutes) ──

FRESHNESS_THRESHOLDS: dict[str, int] = {
    "probable_starters": 60,
    "team_context": 360,       # 6 hours
    "baselines": 120,          # 2 hours
    "lineups": 60,
    "projections": 60,
}

STAGE_ORDER = [
    "probable_starters",
    "team_context",
    "baselines",
    "lineups",
    "projections",
]

_STAGE_RUN_TYPES: dict[str, str] = {
    "probable_starters": "probable_starters_refresh",
    "team_context": "team_context_refresh",
    "baselines": "baseline_refresh",
    "lineups": "lineup_refresh",
    "projections": "projection_build",
}


# ── Freshness check ──

def should_skip_refresh(
    session: Session,
    stage: str,
    target_date: date,
    freshness_minutes: int | None = None,
) -> bool:
    """Check if a stage was recently refreshed and can be skipped."""
    if freshness_minutes is None:
        freshness_minutes = FRESHNESS_THRESHOLDS.get(stage, 60)

    run_type = _STAGE_RUN_TYPES.get(stage)
    if not run_type:
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=freshness_minutes)

    recent = (
        session.query(AppRun)
        .filter(
            AppRun.run_type == run_type,
            AppRun.status == "success",
            AppRun.created_at >= cutoff,
        )
        .first()
    )

    if recent:
        logger.info(
            "Stage '%s' skipped — last success at %s (within %d min)",
            stage, recent.created_at, freshness_minutes,
        )
        return True
    return False


# ── Individual stage runners ──

def _run_stage_starters(session: Session, target_date: date, _season_year: int) -> dict:
    """Refresh probable starters."""
    starters, used_fallback = get_probable_starters(target_date)
    source = "demo-fallback" if used_fallback else "mlb"

    del_count = session.execute(
        delete(ProbableStarter).where(ProbableStarter.game_date == target_date)
    ).rowcount

    for s in starters:
        session.add(ProbableStarter(
            game_date=s.game_date, game_id=s.game_id,
            pitcher_name=s.pitcher_name, pitcher_team=s.pitcher_team,
            opponent_team=s.opponent_team, home_away=s.home_away,
            throws=s.throws, status=s.status, source=s.source,
        ))

    session.add(AppRun(
        run_type="probable_starters_refresh", status="success",
        details=f"date={target_date}, source={source}, count={len(starters)}, "
                f"fallback={used_fallback}, cleared={del_count}",
    ))
    session.flush()

    return {
        "stage": "probable_starters",
        "status": "success",
        "count": len(starters),
        "source": source,
        "fallback": used_fallback,
    }


def _run_stage_team_context(session: Session, _target_date: date, season_year: int) -> dict:
    """Refresh team context."""
    summary = refresh_team_context(session, season_year)

    session.add(AppRun(
        run_type="team_context_refresh", status="success",
        details=f"season={season_year}, source={summary['source']}, "
                f"teams={summary['teams_upserted']}",
    ))
    session.flush()

    return {
        "stage": "team_context",
        "status": "success",
        "teams": summary["teams_upserted"],
        "source": summary["source"],
    }


def _run_stage_baselines(session: Session, target_date: date, season_year: int) -> dict:
    """Refresh baselines for stored starters."""
    stored = (
        session.query(ProbableStarter)
        .filter(ProbableStarter.game_date == target_date)
        .all()
    )
    if not stored:
        return {"stage": "baselines", "status": "skipped", "reason": "no starters stored"}

    starters = [
        StarterRecord(
            game_date=s.game_date, game_id=s.game_id,
            pitcher_name=s.pitcher_name, pitcher_team=s.pitcher_team,
            opponent_team=s.opponent_team, home_away=s.home_away,
            throws=s.throws or "", status=s.status, source=s.source or "",
        )
        for s in stored
    ]

    baselines, fallback_count = get_pitcher_baselines_for_starters(starters, season_year)
    inserted = upsert_pitcher_baselines(session, baselines, season_year)

    session.add(AppRun(
        run_type="baseline_refresh", status="success",
        details=f"date={target_date}, starters={len(starters)}, "
                f"baselines={inserted}, fallback={fallback_count}",
    ))
    session.flush()

    return {
        "stage": "baselines",
        "status": "success",
        "starters": len(starters),
        "baselines": inserted,
        "fallback_count": fallback_count,
    }


def _run_stage_lineups(session: Session, target_date: date, season_year: int) -> dict:
    """Refresh projected lineups and aggregates."""
    summary = refresh_projected_lineups_and_aggregates(session, target_date, season_year)

    session.add(AppRun(
        run_type="lineup_refresh", status="success",
        details=f"date={target_date}, slots={summary['lineup_slots_written']}, "
                f"aggregates={summary['aggregates_written']}",
    ))
    session.flush()

    return {
        "stage": "lineups",
        "status": "success",
        "lineup_slots": summary["lineup_slots_written"],
        "aggregates": summary["aggregates_written"],
        "source": summary.get("source", "unknown"),
    }


def _run_stage_projections(session: Session, target_date: date, season_year: int) -> dict:
    """Build projections from stored data."""
    stored_starters = (
        session.query(ProbableStarter)
        .filter(ProbableStarter.game_date == target_date)
        .all()
    )
    if not stored_starters:
        return {"stage": "projections", "status": "skipped", "reason": "no starters stored"}

    stored_baselines = (
        session.query(PitcherBaseline)
        .filter(PitcherBaseline.season_year == season_year)
        .all()
    )
    if not stored_baselines:
        return {"stage": "projections", "status": "skipped", "reason": "no baselines stored"}

    starters = [
        StarterRecord(
            game_date=s.game_date, game_id=s.game_id,
            pitcher_name=s.pitcher_name, pitcher_team=s.pitcher_team,
            opponent_team=s.opponent_team, home_away=s.home_away,
            throws=s.throws or "", status=s.status, source=s.source or "",
        )
        for s in stored_starters
    ]
    baselines = [
        BaselineRecord(
            pitcher_name=b.pitcher_name, pitcher_team=b.pitcher_team,
            season_year=b.season_year,
            ros_ip_per_start=b.ros_ip_per_start, ros_k_pct=b.ros_k_pct,
            ros_bb_pct=b.ros_bb_pct, ros_era=b.ros_era, ros_whip=b.ros_whip,
            xera=b.xera, xwoba=b.xwoba,
        )
        for b in stored_baselines
    ]

    team_ctx_map = get_team_context_map(session, season_year)
    lineup_agg_map = get_lineup_aggregate_map(session, target_date)

    projections, _ = build_daily_projections_for_starters(
        starters, baselines, team_ctx_map, lineup_agg_map,
    )

    del_count = session.execute(
        delete(DailyPitcherProjection).where(
            DailyPitcherProjection.game_date == target_date,
        )
    ).rowcount

    for p in projections:
        session.add(DailyPitcherProjection(
            game_date=p.game_date, game_id=p.game_id,
            pitcher_name=p.pitcher_name, pitcher_team=p.pitcher_team,
            opponent_team=p.opponent_team, projected_ip=p.projected_ip,
            projected_k=p.projected_k, projected_bb=p.projected_bb,
            projected_h=p.projected_h, projected_er=p.projected_er,
            projected_era=p.projected_era, projected_whip=p.projected_whip,
            win_probability=p.win_probability, blowup_probability=p.blowup_probability,
            confidence=p.confidence, stream_score=p.stream_score,
            k_p20=p.k_p20, k_p50=p.k_p50, k_p80=p.k_p80,
            era_p20=p.era_p20, era_p50=p.era_p50, era_p80=p.era_p80,
            whip_p20=p.whip_p20, whip_p50=p.whip_p50, whip_p80=p.whip_p80,
            model_version=p.model_version,
        ))

    sim_count = sum(1 for p in projections if p.k_p50 is not None)

    session.add(AppRun(
        run_type="projection_build", status="success",
        details=f"date={target_date}, model={MODEL_VERSION}, "
                f"projections={len(projections)}, simulated={sim_count}, "
                f"lineup_aggs={len(lineup_agg_map)}, cleared={del_count}",
    ))
    session.flush()

    return {
        "stage": "projections",
        "status": "success",
        "projections": len(projections),
        "simulated": sim_count,
        "lineup_aggregates": len(lineup_agg_map),
        "model_version": MODEL_VERSION,
        "cleared": del_count,
    }


# ── Stage dispatch ──

_STAGE_RUNNERS = {
    "probable_starters": _run_stage_starters,
    "team_context": _run_stage_team_context,
    "baselines": _run_stage_baselines,
    "lineups": _run_stage_lineups,
    "projections": _run_stage_projections,
}


# ── Public API ──

def run_partial_refresh(
    session: Session,
    stage: str,
    target_date: date,
    season_year: int | None = None,
    force: bool = False,
) -> dict:
    """Refresh a single stage."""
    if stage not in _STAGE_RUNNERS:
        return {"error": f"Unknown stage: {stage}", "valid_stages": STAGE_ORDER}

    if season_year is None:
        season_year = target_date.year

    if not force and should_skip_refresh(session, stage, target_date):
        return {"stage": stage, "status": "skipped", "reason": "fresh"}

    logger.info("Running partial refresh: stage=%s date=%s", stage, target_date)

    try:
        result = _STAGE_RUNNERS[stage](session, target_date, season_year)
        session.commit()
        cache.invalidate()
        return result
    except Exception as e:
        session.rollback()
        logger.error("Stage '%s' failed: %s", stage, e)
        try:
            run_type = _STAGE_RUN_TYPES.get(stage, stage)
            session.add(AppRun(
                run_type=run_type, status="error",
                details=f"date={target_date}, error={e}",
            ))
            session.commit()
        except Exception:
            session.rollback()
        return {"stage": stage, "status": "error", "error": str(e)}


def run_full_refresh(
    session: Session,
    target_date: date,
    season_year: int | None = None,
    force: bool = False,
) -> dict:
    """Run all stages in order. Continue on partial failure."""
    if season_year is None:
        season_year = target_date.year

    logger.info(
        "Starting full refresh: date=%s season=%d force=%s",
        target_date, season_year, force,
    )

    stages: list[dict] = []
    for stage in STAGE_ORDER:
        if not force and should_skip_refresh(session, stage, target_date):
            stages.append({"stage": stage, "status": "skipped", "reason": "fresh"})
            continue

        try:
            result = _STAGE_RUNNERS[stage](session, target_date, season_year)
            session.commit()
            stages.append(result)
        except Exception as e:
            session.rollback()
            logger.error("Stage '%s' failed during full refresh: %s", stage, e)
            stages.append({"stage": stage, "status": "error", "error": str(e)})
            try:
                run_type = _STAGE_RUN_TYPES.get(stage, stage)
                session.add(AppRun(
                    run_type=run_type, status="error",
                    details=f"date={target_date}, error={e}",
                ))
                session.commit()
            except Exception:
                session.rollback()

    cache.invalidate()

    succeeded = sum(1 for s in stages if s.get("status") == "success")
    failed = sum(1 for s in stages if s.get("status") == "error")
    skipped = sum(1 for s in stages if s.get("status") == "skipped")

    logger.info(
        "Full refresh complete: %d succeeded, %d failed, %d skipped",
        succeeded, failed, skipped,
    )

    return {
        "target_date": target_date.isoformat(),
        "season_year": season_year,
        "model_version": MODEL_VERSION,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "stages": stages,
    }
