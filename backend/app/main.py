import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Optional

from fastapi import FastAPI, Query
from sqlalchemy import delete, func

from app.baselines import get_pitcher_baselines_for_starters, upsert_pitcher_baselines
from app.db import SessionLocal, init_db, prune_old_data
from app.lineups import (
    get_lineup_aggregate_map,
    refresh_projected_lineups_and_aggregates,
)
from app.models import (
    AppRun,
    DailyPitcherProjection,
    LineupAggregate,
    PitcherBaseline,
    ProbableStarter,
    ProjectedLineup,
    TeamContext,
)
from app.probable_starters import get_probable_starters
from app.projection_builder import (
    BaselineRecord,
    StarterRecord,
    build_demo_daily_projections,
)
from app.projection_engine import (
    MODEL_VERSION,
    build_daily_projections_for_starters,
)
from app.team_context import get_team_context_map, refresh_team_context

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Helpers ──

def _write_slate_to_db(db, target_date: date, season_year: int = 2026) -> dict:
    """Run the full projection pipeline for a date.
    Uses real MLB starters, real baselines (with per-pitcher fallback),
    then formula-v1 projections. Replaces any existing data for that date."""

    logger.info("Building projection slate for %s", target_date)

    # 1. Get probable starters (real MLB or demo fallback)
    starters, starter_fallback = get_probable_starters(target_date)
    starter_source = "demo-fallback" if starter_fallback else "mlb"

    # 2. Get baselines (real MLB stats with per-pitcher demo fallback)
    baselines, baseline_fallback_count = get_pitcher_baselines_for_starters(starters, season_year)

    # 3. Load team context (auto-seeds defaults if missing)
    team_ctx_map = get_team_context_map(db, season_year)

    # 4. Refresh lineups and load aggregates
    try:
        refresh_projected_lineups_and_aggregates(db, target_date, season_year)
    except Exception as e:
        logger.warning("Lineup refresh failed during slate build: %s — continuing without lineups", e)
    lineup_agg_map = get_lineup_aggregate_map(db, target_date)

    # 5. Build projections using the formula engine with team context + lineups
    projections, _debug = build_daily_projections_for_starters(
        starters, baselines, team_ctx_map, lineup_agg_map,
    )

    # 6. Clear existing rows for this date
    del_ps = db.execute(
        delete(ProbableStarter).where(ProbableStarter.game_date == target_date)
    ).rowcount
    del_dp = db.execute(
        delete(DailyPitcherProjection).where(DailyPitcherProjection.game_date == target_date)
    ).rowcount
    logger.info("Cleared %d starters, %d projections for %s", del_ps, del_dp, target_date)

    # 7. Insert starters
    for s in starters:
        db.add(ProbableStarter(
            game_date=s.game_date, game_id=s.game_id,
            pitcher_name=s.pitcher_name, pitcher_team=s.pitcher_team,
            opponent_team=s.opponent_team, home_away=s.home_away,
            throws=s.throws, status=s.status, source=s.source,
        ))

    # 8. Upsert baselines
    baselines_inserted = upsert_pitcher_baselines(db, baselines, season_year)

    # 9. Insert projections
    for p in projections:
        db.add(DailyPitcherProjection(
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

    db.commit()
    logger.info("Inserted %d starters, %d baselines, %d projections for %s",
                len(starters), baselines_inserted, len(projections), target_date)

    return {
        "target_date": target_date.isoformat(),
        "starter_source": starter_source,
        "starter_fallback": starter_fallback,
        "starters_inserted": len(starters),
        "baselines_inserted": baselines_inserted,
        "baseline_fallback_count": baseline_fallback_count,
        "lineup_aggregates_loaded": len(lineup_agg_map),
        "projections_inserted": len(projections),
        "model_version": MODEL_VERSION,
        "cleared": {
            "probable_starters": del_ps,
            "daily_pitcher_projections": del_dp,
        },
    }


def _refresh_starters_to_db(db, target_date: date) -> dict:
    """Fetch and replace probable starters only (no projections). Returns summary."""

    logger.info("Refreshing probable starters for %s", target_date)
    starters, used_fallback = get_probable_starters(target_date)
    source = "demo-fallback" if used_fallback else "mlb"

    del_count = db.execute(
        delete(ProbableStarter).where(ProbableStarter.game_date == target_date)
    ).rowcount
    logger.info("Cleared %d existing starters for %s", del_count, target_date)

    for s in starters:
        db.add(ProbableStarter(
            game_date=s.game_date, game_id=s.game_id,
            pitcher_name=s.pitcher_name, pitcher_team=s.pitcher_team,
            opponent_team=s.opponent_team, home_away=s.home_away,
            throws=s.throws, status=s.status, source=s.source,
        ))

    db.add(AppRun(
        run_type="probable_starters_refresh", status="success",
        details=f"date={target_date}, source={source}, count={len(starters)}, "
                f"fallback={used_fallback}, cleared={del_count}",
    ))
    db.commit()

    logger.info("Inserted %d starters for %s (source=%s)", len(starters), target_date, source)
    return {
        "target_date": target_date.isoformat(),
        "inserted": len(starters),
        "source": source,
        "used_fallback": used_fallback,
        "cleared": del_count,
    }


def _refresh_baselines_to_db(db, target_date: date, season_year: int = 2026) -> dict:
    """Load stored starters for a date, fetch baselines, upsert. Returns summary."""

    logger.info("Refreshing baselines for starters on %s", target_date)

    # Load stored starters
    stored = (
        db.query(ProbableStarter)
        .filter(ProbableStarter.game_date == target_date)
        .all()
    )

    if not stored:
        msg = f"No stored starters for {target_date} — run refresh-probable-starters first"
        logger.warning(msg)
        return {"error": msg, "target_date": target_date.isoformat()}

    # Convert ORM rows to StarterRecords for the baseline builder
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
    inserted = upsert_pitcher_baselines(db, baselines, season_year)

    db.add(AppRun(
        run_type="baseline_refresh", status="success",
        details=f"date={target_date}, starters={len(starters)}, "
                f"baselines={inserted}, fallback={fallback_count}",
    ))
    db.commit()

    logger.info("Baseline refresh: %d starters, %d baselines, %d fallback",
                len(starters), inserted, fallback_count)
    return {
        "target_date": target_date.isoformat(),
        "starters": len(starters),
        "baselines_refreshed": inserted,
        "fallback_count": fallback_count,
    }


def _projection_row_to_dict(row: DailyPitcherProjection, starter: ProbableStarter | None = None) -> dict:
    """Convert a projection ORM row to a clean API dict."""
    d = {
        "pitcher_name": row.pitcher_name,
        "pitcher_team": row.pitcher_team,
        "opponent_team": row.opponent_team,
        "home_away": starter.home_away if starter else None,
        "projected_ip": row.projected_ip,
        "projected_k": row.projected_k,
        "projected_bb": row.projected_bb,
        "projected_h": row.projected_h,
        "projected_er": row.projected_er,
        "projected_era": row.projected_era,
        "projected_whip": row.projected_whip,
        "win_probability": row.win_probability,
        "blowup_probability": row.blowup_probability,
        "confidence": row.confidence,
        "stream_score": row.stream_score,
        "k_p20": row.k_p20,
        "k_p50": row.k_p50,
        "k_p80": row.k_p80,
        "era_p20": row.era_p20,
        "era_p50": row.era_p50,
        "era_p80": row.era_p80,
        "whip_p20": row.whip_p20,
        "whip_p50": row.whip_p50,
        "whip_p80": row.whip_p80,
        "model_version": row.model_version,
        "generated_at": str(row.generated_at) if row.generated_at else None,
    }
    return d


# ── Lifespan ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting mlb-streamer-backend...")
    init_db()

    db = SessionLocal()
    try:
        logger.info("Running startup prune...")
        counts = prune_old_data(db)
        logger.info("Prune complete: %s", counts)

        db.add(AppRun(run_type="startup", status="success", details=f"pruned: {counts}"))
        db.commit()
        logger.info("Startup app_run logged")
    except Exception as e:
        logger.error("Startup error: %s", e)
        db.rollback()
    finally:
        db.close()

    yield
    logger.info("Shutting down mlb-streamer-backend")


app = FastAPI(title="mlb-streamer-backend", lifespan=lifespan)


# ── Health ──

@app.get("/health")
def health():
    return {"status": "ok"}


# ── Projections ──

@app.get("/projections/tomorrow")
def projections_tomorrow(
    date: Optional[str] = Query(None, description="Override date as YYYY-MM-DD"),
    min_confidence: Optional[str] = Query(None, description="Filter: low, medium, high"),
    limit: int = Query(50, ge=1, le=200),
):
    target = _parse_date(date) if date else _tomorrow()

    logger.info("Fetching projections for %s (min_confidence=%s, limit=%d)",
                target, min_confidence, limit)

    db = SessionLocal()
    try:
        query = (
            db.query(DailyPitcherProjection)
            .filter(DailyPitcherProjection.game_date == target)
        )

        if min_confidence:
            confidence_order = {"high": 3, "medium": 2, "low": 1}
            min_level = confidence_order.get(min_confidence.lower(), 0)
            valid = [k for k, v in confidence_order.items() if v >= min_level]
            query = query.filter(DailyPitcherProjection.confidence.in_(valid))

        rows = (
            query
            .order_by(DailyPitcherProjection.stream_score.desc())
            .limit(limit)
            .all()
        )

        starter_map: dict[str, ProbableStarter] = {}
        if rows:
            starters = (
                db.query(ProbableStarter)
                .filter(ProbableStarter.game_date == target)
                .all()
            )
            for s in starters:
                starter_map[s.pitcher_name] = s

        latest_gen = max((r.generated_at for r in rows), default=None) if rows else None

        return {
            "date": target.isoformat(),
            "count": len(rows),
            "generated_at": str(latest_gen) if latest_gen else None,
            "pitchers": [
                _projection_row_to_dict(r, starter_map.get(r.pitcher_name))
                for r in rows
            ],
        }
    finally:
        db.close()


@app.get("/projections/{pitcher_name}")
def projections_by_pitcher(pitcher_name: str):
    logger.info("Fetching projection history for %s", pitcher_name)

    db = SessionLocal()
    try:
        rows = (
            db.query(DailyPitcherProjection)
            .filter(DailyPitcherProjection.pitcher_name == pitcher_name)
            .order_by(DailyPitcherProjection.generated_at.desc())
            .limit(5)
            .all()
        )

        return {
            "pitcher_name": pitcher_name,
            "count": len(rows),
            "projections": [_projection_row_to_dict(r) for r in rows],
        }
    finally:
        db.close()


# ── Admin ──

@app.get("/admin/db-summary")
def db_summary():
    db = SessionLocal()
    try:
        ps_count = db.query(func.count(ProbableStarter.id)).scalar()
        pb_count = db.query(func.count(PitcherBaseline.id)).scalar()
        dp_count = db.query(func.count(DailyPitcherProjection.id)).scalar()
        tc_count = db.query(func.count(TeamContext.id)).scalar()
        pl_count = db.query(func.count(ProjectedLineup.id)).scalar()
        la_count = db.query(func.count(LineupAggregate.id)).scalar()
        ar_count = db.query(func.count(AppRun.id)).scalar()

        latest = db.query(func.max(DailyPitcherProjection.generated_at)).scalar()

        return {
            "probable_starters": ps_count,
            "pitcher_baselines": pb_count,
            "daily_pitcher_projections": dp_count,
            "team_context": tc_count,
            "projected_lineups": pl_count,
            "lineup_aggregates": la_count,
            "app_runs": ar_count,
            "latest_projection": str(latest) if latest else None,
        }
    finally:
        db.close()


@app.get("/admin/probable-starters")
def admin_probable_starters(
    date: Optional[str] = Query(None, description="Date as YYYY-MM-DD, defaults to tomorrow"),
):
    target = _parse_date(date) if date else _tomorrow()
    logger.info("Fetching stored probable starters for %s", target)

    db = SessionLocal()
    try:
        rows = (
            db.query(ProbableStarter)
            .filter(ProbableStarter.game_date == target)
            .order_by(ProbableStarter.pitcher_team, ProbableStarter.pitcher_name)
            .all()
        )

        return {
            "date": target.isoformat(),
            "count": len(rows),
            "starters": [
                {
                    "pitcher_name": r.pitcher_name,
                    "pitcher_team": r.pitcher_team,
                    "opponent_team": r.opponent_team,
                    "home_away": r.home_away,
                    "throws": r.throws,
                    "status": r.status,
                    "source": r.source,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@app.get("/admin/pitcher-baselines")
def admin_pitcher_baselines(
    pitcher_name: Optional[str] = Query(None),
    season_year: Optional[int] = Query(None),
    limit: int = Query(25, ge=1, le=200),
):
    db = SessionLocal()
    try:
        query = db.query(PitcherBaseline)
        if pitcher_name:
            query = query.filter(PitcherBaseline.pitcher_name == pitcher_name)
        if season_year:
            query = query.filter(PitcherBaseline.season_year == season_year)

        rows = (
            query
            .order_by(PitcherBaseline.updated_at.desc())
            .limit(limit)
            .all()
        )

        return {
            "count": len(rows),
            "baselines": [
                {
                    "pitcher_name": r.pitcher_name,
                    "pitcher_team": r.pitcher_team,
                    "season_year": r.season_year,
                    "ros_ip_per_start": r.ros_ip_per_start,
                    "ros_k_pct": r.ros_k_pct,
                    "ros_bb_pct": r.ros_bb_pct,
                    "ros_era": r.ros_era,
                    "ros_whip": r.ros_whip,
                    "xera": r.xera,
                    "xwoba": r.xwoba,
                    "updated_at": str(r.updated_at) if r.updated_at else None,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@app.post("/admin/refresh-probable-starters")
def admin_refresh_probable_starters(
    date: Optional[str] = Query(None, description="Date as YYYY-MM-DD, defaults to tomorrow"),
):
    target = _parse_date(date) if date else _tomorrow()

    db = SessionLocal()
    try:
        summary = _refresh_starters_to_db(db, target)
        return summary
    except Exception as e:
        db.rollback()
        logger.error("Probable starter refresh failed: %s", e)

        try:
            db.add(AppRun(
                run_type="probable_starters_refresh", status="error",
                details=f"date={target}, error={e}",
            ))
            db.commit()
        except Exception:
            db.rollback()

        return {"error": str(e)}
    finally:
        db.close()


@app.post("/admin/refresh-baselines")
def admin_refresh_baselines(
    date: Optional[str] = Query(None, description="Date as YYYY-MM-DD, defaults to tomorrow"),
):
    target = _parse_date(date) if date else _tomorrow()

    db = SessionLocal()
    try:
        summary = _refresh_baselines_to_db(db, target)
        return summary
    except Exception as e:
        db.rollback()
        logger.error("Baseline refresh failed: %s", e)

        try:
            db.add(AppRun(
                run_type="baseline_refresh", status="error",
                details=f"date={target}, error={e}",
            ))
            db.commit()
        except Exception:
            db.rollback()

        return {"error": str(e)}
    finally:
        db.close()


@app.get("/admin/last-run-status")
def admin_last_run_status():
    db = SessionLocal()
    try:
        rows = (
            db.query(AppRun)
            .order_by(AppRun.created_at.desc())
            .limit(10)
            .all()
        )

        return {
            "count": len(rows),
            "runs": [
                {
                    "run_type": r.run_type,
                    "status": r.status,
                    "details": r.details,
                    "created_at": str(r.created_at) if r.created_at else None,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@app.post("/admin/prune-now")
def prune_now():
    db = SessionLocal()
    try:
        counts = prune_old_data(db)
        db.add(AppRun(run_type="prune", status="success", details=f"deleted: {counts}"))
        db.commit()
        logger.info("Manual prune complete: %s", counts)
        return {"deleted": counts}
    except Exception as e:
        db.rollback()
        logger.error("Prune failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()


@app.post("/admin/build-demo-projections")
def build_demo_projections(
    date: Optional[str] = Query(None, description="Target date as YYYY-MM-DD, defaults to tomorrow"),
):
    target = _parse_date(date) if date else _tomorrow()

    db = SessionLocal()
    try:
        summary = _write_slate_to_db(db, target)

        db.add(AppRun(
            run_type="projection_build", status="success",
            details=f"date={target}, starter_source={summary['starter_source']}, "
                    f"starters={summary['starters_inserted']}, "
                    f"baselines={summary['baselines_inserted']}, "
                    f"baseline_fallbacks={summary['baseline_fallback_count']}, "
                    f"projections={summary['projections_inserted']}",
        ))
        db.commit()

        return summary
    except Exception as e:
        db.rollback()
        logger.error("Projection build failed: %s", e)

        try:
            db.add(AppRun(
                run_type="projection_build", status="error",
                details=f"date={target}, error={e}",
            ))
            db.commit()
        except Exception:
            db.rollback()

        return {"error": str(e)}
    finally:
        db.close()


@app.post("/admin/build-projections")
def build_projections(
    date: Optional[str] = Query(None, description="Target date as YYYY-MM-DD, defaults to tomorrow"),
    season_year: int = Query(2026, description="Season year for baselines"),
):
    """Build projections from stored starters, baselines, and team context.

    Loads starters and baselines already in the DB, loads team context
    (auto-seeding defaults if needed), runs the projection engine,
    and stores results. Use refresh-probable-starters and refresh-baselines
    first to populate upstream data.
    """
    target = _parse_date(date) if date else _tomorrow()

    db = SessionLocal()
    try:
        # Load stored starters
        stored_starters = (
            db.query(ProbableStarter)
            .filter(ProbableStarter.game_date == target)
            .all()
        )
        if not stored_starters:
            return {"error": f"No stored starters for {target} — run refresh-probable-starters first"}

        # Load stored baselines
        stored_baselines = (
            db.query(PitcherBaseline)
            .filter(PitcherBaseline.season_year == season_year)
            .all()
        )
        if not stored_baselines:
            return {"error": f"No stored baselines for {season_year} — run refresh-baselines first"}

        # Convert ORM rows to pipeline records
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

        # Load team context (auto-seeds defaults if missing)
        team_ctx_map = get_team_context_map(db, season_year)

        # Load lineup aggregates (if available)
        lineup_agg_map = get_lineup_aggregate_map(db, target)

        # Run formula engine with team context + lineup aggregates
        projections, debug_list = build_daily_projections_for_starters(
            starters, baselines, team_ctx_map, lineup_agg_map,
        )

        # Clear existing projections for this date
        del_count = db.execute(
            delete(DailyPitcherProjection).where(DailyPitcherProjection.game_date == target)
        ).rowcount

        # Insert new projections
        for p in projections:
            db.add(DailyPitcherProjection(
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

        # Count how many projections have simulation data
        sim_count = sum(1 for p in projections if p.k_p50 is not None)

        db.add(AppRun(
            run_type="projection_build", status="success",
            details=f"date={target}, model={MODEL_VERSION}, "
                    f"starters={len(starters)}, baselines={len(baselines)}, "
                    f"team_ctx={len(team_ctx_map)}, "
                    f"lineup_aggs={len(lineup_agg_map)}, "
                    f"projections={len(projections)}, simulated={sim_count}, "
                    f"cleared={del_count}",
        ))
        db.commit()

        logger.info("Built %d projections for %s (%d simulated, cleared %d old)",
                     len(projections), target, sim_count, del_count)

        return {
            "target_date": target.isoformat(),
            "model_version": MODEL_VERSION,
            "starters_loaded": len(starters),
            "baselines_loaded": len(baselines),
            "team_contexts_loaded": len(team_ctx_map),
            "lineup_aggregates_loaded": len(lineup_agg_map),
            "projections_built": len(projections),
            "simulations_completed": sim_count,
            "cleared": del_count,
        }
    except Exception as e:
        db.rollback()
        logger.error("Projection build failed: %s", e)

        try:
            db.add(AppRun(
                run_type="projection_build", status="error",
                details=f"date={target}, model={MODEL_VERSION}, error={e}",
            ))
            db.commit()
        except Exception:
            db.rollback()

        return {"error": str(e)}
    finally:
        db.close()


@app.get("/admin/projection-debug")
def projection_debug(
    date: Optional[str] = Query(None, description="Target date as YYYY-MM-DD, defaults to tomorrow"),
    season_year: int = Query(2026, description="Season year for baselines"),
    pitcher_name: Optional[str] = Query(None, description="Filter to a single pitcher"),
):
    """Return debug info for each pitcher's projection — baseline inputs,
    matchup context, intermediate values, and score breakdown.

    Does NOT write to DB; runs the engine in read-only mode against stored data.
    """
    target = _parse_date(date) if date else _tomorrow()

    db = SessionLocal()
    try:
        # Load stored starters
        starter_query = db.query(ProbableStarter).filter(ProbableStarter.game_date == target)
        if pitcher_name:
            starter_query = starter_query.filter(ProbableStarter.pitcher_name == pitcher_name)
        stored_starters = starter_query.all()

        if not stored_starters:
            return {"error": f"No stored starters for {target}", "date": target.isoformat()}

        # Load stored baselines
        stored_baselines = (
            db.query(PitcherBaseline)
            .filter(PitcherBaseline.season_year == season_year)
            .all()
        )

        # Convert to pipeline records
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

        # Load team context (auto-seeds defaults if missing)
        team_ctx_map = get_team_context_map(db, season_year)

        # Load lineup aggregates (if available)
        lineup_agg_map = get_lineup_aggregate_map(db, target)

        # Run engine (read-only — we only want the debug output)
        projections, debug_list = build_daily_projections_for_starters(
            starters, baselines, team_ctx_map, lineup_agg_map,
        )

        return {
            "date": target.isoformat(),
            "model_version": MODEL_VERSION,
            "team_contexts_loaded": len(team_ctx_map),
            "lineup_aggregates_loaded": len(lineup_agg_map),
            "pitcher_count": len(debug_list),
            "pitchers": debug_list,
        }
    finally:
        db.close()


@app.get("/admin/simulation-debug")
def simulation_debug(
    date: Optional[str] = Query(None, description="Target date as YYYY-MM-DD, defaults to tomorrow"),
    pitcher_name: Optional[str] = Query(None, description="Filter to a single pitcher"),
):
    """Return simulation debug info for each pitcher — deterministic values,
    simulation summary, percentiles, and refined probabilities.

    Does NOT write to DB; reads stored projections with their simulation outputs.
    """
    target = _parse_date(date) if date else _tomorrow()

    db = SessionLocal()
    try:
        query = (
            db.query(DailyPitcherProjection)
            .filter(DailyPitcherProjection.game_date == target)
        )
        if pitcher_name:
            query = query.filter(DailyPitcherProjection.pitcher_name == pitcher_name)

        rows = query.order_by(DailyPitcherProjection.stream_score.desc()).all()

        if not rows:
            return {"error": f"No projections for {target}", "date": target.isoformat()}

        pitchers = []
        for r in rows:
            has_sim = r.k_p50 is not None
            entry = {
                "pitcher_name": r.pitcher_name,
                "pitcher_team": r.pitcher_team,
                "opponent_team": r.opponent_team,
                "deterministic": {
                    "projected_ip": r.projected_ip,
                    "projected_k": r.projected_k,
                    "projected_bb": r.projected_bb,
                    "projected_h": r.projected_h,
                    "projected_er": r.projected_er,
                    "projected_era": r.projected_era,
                    "projected_whip": r.projected_whip,
                },
                "simulation": {
                    "available": has_sim,
                    "win_probability": r.win_probability,
                    "blowup_probability": r.blowup_probability,
                    "k_p20": r.k_p20,
                    "k_p50": r.k_p50,
                    "k_p80": r.k_p80,
                    "era_p20": r.era_p20,
                    "era_p50": r.era_p50,
                    "era_p80": r.era_p80,
                    "whip_p20": r.whip_p20,
                    "whip_p50": r.whip_p50,
                    "whip_p80": r.whip_p80,
                },
                "confidence": r.confidence,
                "stream_score": r.stream_score,
                "model_version": r.model_version,
            }
            pitchers.append(entry)

        return {
            "date": target.isoformat(),
            "model_version": MODEL_VERSION,
            "pitcher_count": len(pitchers),
            "pitchers": pitchers,
        }
    finally:
        db.close()


@app.post("/admin/refresh-team-context")
def admin_refresh_team_context(
    season_year: int = Query(2026, description="Season year"),
):
    """Refresh team context from real MLB Stats API data, falling back to
    static defaults if the API is unavailable."""
    db = SessionLocal()
    try:
        summary = refresh_team_context(db, season_year)

        db.add(AppRun(
            run_type="team_context_refresh", status="success",
            details=f"season_year={season_year}, source={summary['source']}, "
                    f"teams={summary['teams_upserted']}, "
                    f"fallback={summary['fallback_used']}",
        ))
        db.commit()

        logger.info(
            "Team context refresh: %d teams for %d (source=%s, fallback=%s)",
            summary["teams_upserted"], season_year,
            summary["source"], summary["fallback_used"],
        )
        return summary
    except Exception as e:
        db.rollback()
        logger.error("Team context refresh failed: %s", e)

        try:
            db.add(AppRun(
                run_type="team_context_refresh", status="error",
                details=f"season_year={season_year}, error={e}",
            ))
            db.commit()
        except Exception:
            db.rollback()

        return {"error": str(e)}
    finally:
        db.close()


@app.get("/admin/team-context")
def admin_team_context(
    season_year: int = Query(2026, description="Season year"),
):
    """Return all stored team context rows for a season, sorted by team_code."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    freshness_cutoff = now - timedelta(hours=24)

    db = SessionLocal()
    try:
        rows = (
            db.query(TeamContext)
            .filter(TeamContext.season_year == season_year)
            .order_by(TeamContext.team_code)
            .all()
        )

        return {
            "season_year": season_year,
            "count": len(rows),
            "teams": [
                {
                    "team_code": r.team_code,
                    "offense_strength": r.offense_strength,
                    "offense_k_tendency": r.offense_k_tendency,
                    "win_support_factor": r.win_support_factor,
                    "bullpen_support_factor": r.bullpen_support_factor,
                    "run_environment_factor": r.run_environment_factor,
                    "updated_at": str(r.updated_at) if r.updated_at else None,
                    "recently_updated": bool(r.updated_at and r.updated_at >= freshness_cutoff),
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@app.post("/admin/refresh-lineups")
def admin_refresh_lineups(
    date: Optional[str] = Query(None, description="Date as YYYY-MM-DD, defaults to tomorrow"),
    season_year: int = Query(2026, description="Season year for hitter stats"),
):
    """Refresh projected lineups and aggregates from the MLB API.

    Fetches batting orders from game feeds, pulls each hitter's season stats,
    and builds per-team aggregates for lineup-aware projections.
    Falls back to neutral aggregates if the API is unavailable.
    """
    target = _parse_date(date) if date else _tomorrow()

    db = SessionLocal()
    try:
        summary = refresh_projected_lineups_and_aggregates(db, target, season_year)

        db.add(AppRun(
            run_type="lineup_refresh", status="success",
            details=f"date={target}, slots={summary['lineup_slots_written']}, "
                    f"aggregates={summary['aggregates_written']}, "
                    f"source={summary['source']}, fallback={summary['fallback_used']}",
        ))
        db.commit()

        logger.info("Lineup refresh: %s", summary)
        return summary
    except Exception as e:
        db.rollback()
        logger.error("Lineup refresh failed: %s", e)

        try:
            db.add(AppRun(
                run_type="lineup_refresh", status="error",
                details=f"date={target}, error={e}",
            ))
            db.commit()
        except Exception:
            db.rollback()

        return {"error": str(e)}
    finally:
        db.close()


@app.get("/admin/lineups")
def admin_lineups(
    date: Optional[str] = Query(None, description="Date as YYYY-MM-DD, defaults to tomorrow"),
    team_code: Optional[str] = Query(None, description="Filter by team code"),
):
    """Return stored projected lineups for a date."""
    target = _parse_date(date) if date else _tomorrow()

    db = SessionLocal()
    try:
        query = db.query(ProjectedLineup).filter(ProjectedLineup.game_date == target)
        if team_code:
            query = query.filter(ProjectedLineup.team_code == team_code)

        rows = query.order_by(
            ProjectedLineup.team_code, ProjectedLineup.batting_order
        ).all()

        return {
            "date": target.isoformat(),
            "count": len(rows),
            "lineups": [
                {
                    "team_code": r.team_code,
                    "game_id": r.game_id,
                    "batting_order": r.batting_order,
                    "hitter_name": r.hitter_name,
                    "bats": r.bats,
                    "confirmed": r.confirmed,
                    "source": r.source,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@app.get("/admin/lineup-aggregates")
def admin_lineup_aggregates(
    date: Optional[str] = Query(None, description="Date as YYYY-MM-DD, defaults to tomorrow"),
    team_code: Optional[str] = Query(None, description="Filter by team code"),
):
    """Return stored lineup aggregates for a date."""
    target = _parse_date(date) if date else _tomorrow()

    db = SessionLocal()
    try:
        query = db.query(LineupAggregate).filter(LineupAggregate.game_date == target)
        if team_code:
            query = query.filter(LineupAggregate.team_code == team_code)

        rows = query.order_by(LineupAggregate.team_code).all()

        return {
            "date": target.isoformat(),
            "count": len(rows),
            "aggregates": [
                {
                    "team_code": r.team_code,
                    "game_id": r.game_id,
                    "vs_hand": r.vs_hand,
                    "agg_k_tendency": r.agg_k_tendency,
                    "agg_bb_tendency": r.agg_bb_tendency,
                    "agg_offense_strength": r.agg_offense_strength,
                    "agg_contact_quality": r.agg_contact_quality,
                    "hitter_count": r.hitter_count,
                    "source": r.source,
                    "created_at": str(r.created_at) if r.created_at else None,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@app.post("/admin/seed-demo-data")
def seed_demo_data():
    """Convenience alias — builds projections for tomorrow."""
    return build_demo_projections()


# ── Utilities ──

def _tomorrow() -> date:
    return date.today() + timedelta(days=1)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)
