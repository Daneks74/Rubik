import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import SessionLocal, init_db
from app.models import HealthCheck

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting mlb-streamer-backend...")
    init_db()
    yield
    logger.info("Shutting down mlb-streamer-backend")


app = FastAPI(title="mlb-streamer-backend", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/db-test")
def db_test():
    db = SessionLocal()
    try:
        row = HealthCheck(message="connected")
        db.add(row)
        db.commit()

        rows = db.query(HealthCheck).all()
        return [
            {"id": r.id, "message": r.message, "created_at": str(r.created_at)}
            for r in rows
        ]
    finally:
        db.close()
