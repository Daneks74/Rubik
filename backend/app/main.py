import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI
from sqlalchemy import func

from app.db import SessionLocal, init_db, prune_old_data
from app.models import (
    AppRun,
    DailyPitcherProjection,
    PitcherBaseline,
    ProbableStarter,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


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


@app.post("/admin/seed-demo-data")
def seed_demo_data():
    db = SessionLocal()
    try:
        tomorrow = date.today() + timedelta(days=1)
        game_id = f"mlb-{tomorrow.isoformat()}-demo"

        starters = [
            ProbableStarter(
                game_date=tomorrow, game_id=f"{game_id}-1",
                pitcher_name="Corbin Burnes", pitcher_team="Ari",
                opponent_team="LAD", home_away="home",
                throws="R", status="probable", source="demo",
            ),
            ProbableStarter(
                game_date=tomorrow, game_id=f"{game_id}-2",
                pitcher_name="Chris Sale", pitcher_team="Atl",
                opponent_team="NYM", home_away="away",
                throws="L", status="probable", source="demo",
            ),
        ]

        baselines = [
            PitcherBaseline(
                pitcher_name="Corbin Burnes", pitcher_team="Ari",
                season_year=2026, ros_ip_per_start=6.2,
                ros_k_pct=26.5, ros_bb_pct=5.1,
                ros_era=3.15, ros_whip=1.08,
                xera=3.02, xwoba=0.285,
            ),
            PitcherBaseline(
                pitcher_name="Chris Sale", pitcher_team="Atl",
                season_year=2026, ros_ip_per_start=5.8,
                ros_k_pct=28.9, ros_bb_pct=6.3,
                ros_era=3.40, ros_whip=1.12,
                xera=3.25, xwoba=0.290,
            ),
        ]

        projections = [
            DailyPitcherProjection(
                game_date=tomorrow, game_id=f"{game_id}-1",
                pitcher_name="Corbin Burnes", pitcher_team="Ari",
                opponent_team="LAD",
                projected_ip=6.1, projected_k=7.2,
                projected_bb=1.8, projected_h=5.0,
                projected_er=2.5, projected_era=3.69,
                projected_whip=1.11, win_probability=0.52,
                blowup_probability=0.14, confidence="high",
                stream_score=72.5, model_version="v0.1-demo",
            ),
            DailyPitcherProjection(
                game_date=tomorrow, game_id=f"{game_id}-2",
                pitcher_name="Chris Sale", pitcher_team="Atl",
                opponent_team="NYM",
                projected_ip=5.7, projected_k=7.8,
                projected_bb=2.1, projected_h=5.5,
                projected_er=3.0, projected_era=4.74,
                projected_whip=1.33, win_probability=0.45,
                blowup_probability=0.22, confidence="medium",
                stream_score=58.3, model_version="v0.1-demo",
            ),
        ]

        db.add_all(starters + baselines + projections)
        db.commit()

        db.add(AppRun(run_type="seed_demo", status="success", details="6 demo rows inserted"))
        db.commit()

        logger.info("Demo data seeded: 2 starters, 2 baselines, 2 projections")
        return {
            "seeded": {
                "probable_starters": len(starters),
                "pitcher_baselines": len(baselines),
                "daily_pitcher_projections": len(projections),
            }
        }
    except Exception as e:
        db.rollback()
        logger.error("Seed failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()
