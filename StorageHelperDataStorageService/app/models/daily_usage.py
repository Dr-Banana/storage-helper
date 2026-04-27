"""
Database model for tracking daily per-user API usage (free tier enforcement)
"""
from sqlalchemy import Column, Integer, Date, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.core.database import Base


class DailyUsage(Base):
    """Tracks meal plan session count and token consumption per user per day."""
    __tablename__ = "daily_usage"
    __table_args__ = (
        UniqueConstraint("user_id", "usage_date", name="uq_daily_usage_user_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    usage_date = Column(Date, nullable=False)
    meal_plan_sessions = Column(Integer, nullable=False, default=0)
    token_count = Column(Integer, nullable=False, default=0)

    user = relationship("User", back_populates="daily_usages")

    def __repr__(self):
        return f"<DailyUsage(user_id={self.user_id}, date={self.usage_date}, sessions={self.meal_plan_sessions}, tokens={self.token_count})>"
