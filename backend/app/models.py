from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String

from app.db import Base


class HealthCheck(Base):
    __tablename__ = "health_check"

    id = Column(Integer, primary_key=True, index=True)
    message = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
