import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Optional

from fastapi import FastAPI, Query
from sqlalchemy import delete, func

from app.db import SessionLocal, init_db, prune_old_data
from app.models import (
    AppRun,
    DailyPitcherProjection,
    PitcherBaseline,
    ProbableStarter,
)
from app.projection_builder import (
    MODEL_VERSION,
    build_demo_baselines,
    build_demo_daily_projections,
    build_demo_probable_starters,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Helpers ──

def _write_slate_to_db(db, target_date: date, season_year: int = 2026) -> dict:
    """Run the full demo pipeline for a date: build starters, baselines, projections.
    Replaces any existing data for that date. Returns a summary dict."""

    logger.info("Building demo slate for %s", target_date)

    # 1. Build demo data
    starters = build_demo_probable_starters(target_date)
    baselines = build_demo_baselines(starters, season_year)
    projections = build_demo_daily_projections(starters, baselines)

    # 2. Clear existing rows for this date
    del_ps = db.execute(
        delete(ProbableStarter).where(ProbableStarter.game_date == target_date)
    ).rowcount
    del_dp = db.execute(
        delete(DailyPitcherProjection).where(DailyPitcherProjection.game_date == target_date)
    ).rowcount
    logger.info("Cleared %d starters, %d projections for %s", del_ps, del_dp, target_date)

    # 3. Replace baselines for these pitchers (simpler than upsert for demo)
    pitcher_names = [s.pitcher_name for s in starters]
    del_bl = db.execute(
        delete(PitcherBaseline).where(
            PitcherBaseline.pitcher_name.in_(pitcher_names),
            PitcherBaseline.season_year == season_year,
        )
    ).rowcount
    logger.info("Cleared %d baselines for slate pitchers", del_bl)

    # 4. Insert new rows
    for s in starters:
        db.add(ProbableStarter(
            game_date=s.game_date, game_id=s.game_id,
            pitcher_name=s.pitcher_name, pitcher_team=s.pitcher_team,
            opponent_team=s.opponent_team, home_away=s.home_away,
            throws=s.throws, status=s.status, source=s.source,
        ))

    for bl in baselines:
        db.add(PitcherBaseline(
            pitcher_name=bl.pitcher_name, pitcher_team=bl.pitcher_team,
            season_year=bl.season_year, ros_ip_per_start=bl.ros_ip_per_start,
            ros_k_pct=bl.ros_k_pct, ros_bb_pct=bl.ros_bb_pct,
            ros_era=bl.ros_era, ros_whip=bl.ros_whip,
            xera=bl.xera, xwoba=bl.xwoba,
        ))

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
            model_version=p.model_version,
        ))

    db.commit()
    logger.info("Inserted %d starters, %d baselines, %d projections for %s",
                len(starters), len(baselines), len(projections), target_date)

    return {
        "target_date": target_date.isoformat(),
        "starters_inserted": len(starters),
        "baselines_inserted": len(baselines),
        "projections_inserted": len(projections),
        "model_version": MODEL_VERSION,
        "cleared": {
            "probable_starters": del_ps,
            "pitcher_baselines": del_bl,
            "daily_pitcher_projections": del_dp,
        },
    }


def _projection_row_to_dict(row: DailyPitcherProjection, starter: ProbableStarter | None = None) -> dict:
    """Convert a projection ORM row to a clean API dict."""
    return {
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
        "model_version": row.model_version,
        "generated_at": str(row.generated_at) if row.generated_at else None,
    }


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
    if date:
        target = _parse_date(date)
    else:
        target = _tomorrow()

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

        # Build a lookup for home_away from probable_starters
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
        ar_count = db.query(func.count(AppRun.id)).scalar()

        latest = db.query(func.max(DailyPitcherProjection.generated_at)).scalar()

        return {
            "probable_starters": ps_count,
            "pitcher_baselines": pb_count,
            "daily_pitcher_projections": dp_count,
            "app_runs": ar_count,
            "latest_projection": str(latest) if latest else None,
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
    if date:
        target = _parse_date(date)
    else:
        target = _tomorrow()

    db = SessionLocal()
    try:
        summary = _write_slate_to_db(db, target)

        db.add(AppRun(
            run_type="projection_build", status="success",
            details=f"date={target}, starters={summary['starters_inserted']}, "
                    f"baselines={summary['baselines_inserted']}, "
                    f"projections={summary['projections_inserted']}",
        ))
        db.commit()

        return summary
    except Exception as e:
        db.rollback()
        logger.error("Demo projection build failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()


@app.post("/admin/seed-demo-data")
def seed_demo_data():
    """Convenience alias — builds demo projections for tomorrow."""
    return build_demo_projections()


# ── Utilities ──

def _tomorrow() -> date:
    return date.today() + timedelta(days=1)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)
